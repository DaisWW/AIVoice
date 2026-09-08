import { ApiClient } from "./core/api-client.js";
import { AuthController } from "./auth/auth-controller.js?v=20260825.1";
import { escapeHtml, setButtonBusy } from "./core/dom.js";
import { safeResourceUrl } from "./core/url.js?v=20260826.1";
import { GenerationConfigurationController } from "./generation/configuration-controller.js?v=20260831.2";
import { generationSeedPlan } from "./generation/seed-plan.js";

const byId = (id) => document.getElementById(id);
const enc = (value) => encodeURIComponent(value);
const statusLabel = (status) => ({ queued: "排队中", running: "生成中", completed: "已完成", failed: "失败" }[status] || status);
const roleLabel = (role) => ({ owner: "负责人", admin: "项目管理员", member: "项目成员" }[role] || "项目成员");
const formatDate = (value) => value ? new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "--";
const formatBytes = (value) => Number(value || 0) < 1024 * 1024 ? `${Math.max(1, Math.round(Number(value || 0) / 1024))} KB` : `${(Number(value || 0) / 1024 / 1024).toFixed(1)} MB`;
const first = (value) => String(value || "").trim().slice(0, 1).toUpperCase() || "?";
const WORKSTATION_POSITION_KEY = "voice-lab-workstation-position";
const WORKSTATION_VIEWS = new Set(["script", "voice", "project"]);

class WorkstationApp {
  constructor() {
    this.api = new ApiClient();
    this.state = { user: null, project: null, projects: [], scripts: [], voices: [], jobs: [], config: { models: [] }, scriptImportBatch: null, scriptImportError: "", selectedScriptId: null, selectedVoiceId: null, selectedModelId: null, generationConfigurations: [], generationRequest: null, currentView: "script", projectPromptSuggestion: "", projectPromptDraft: null, scriptPromptSuggestion: "", scriptPromptDraft: null, generatedLines: [], generatedLinesScriptId: null, lineRewriteSuggestions: {}, lineRewriteInstructions: {}, scriptItemDrafts: {}, pronunciationSuggestions: [], pronunciationSuggestionsScriptId: null, textGenerationDrafts: {}, scriptSearchQuery: "", voiceSearchQuery: "", settingsDrawer: null, assetRename: null, jobDetails: {}, textRuns: [], contextRevisions: { project: [], script: [] }, textRunParentId: null };
    this.auth = new AuthController(this.api, "appShell", (user) => this.boot(user));
    this.generation = new GenerationConfigurationController(this.state, {
      storePosition: () => this.storePosition(),
      toast: (message, error = false) => this.toast(message, error),
    });
    this.requestVersion = 0;
    this.loadingScriptToken = 0;
    this.loadingScriptPromise = null;
    this.loadingVoiceToken = 0;
    this.loadingMembersToken = 0;
    this.toastTimer = null;
    this.loadingJobIds = new Set();
    this.textRunPolling = new Set();
    this.textRunPollFailures = new Map();
  }

  async start() {
    this.auth.bind();
    this.bind();
    const user = await this.auth.restore();
    if (user) await this.boot(user);
    window.setInterval(() => { if (!document.hidden) void this.refreshJobs(); }, 5000);
    document.documentElement.classList.remove("app-loading");
  }

  bind() {
    byId("projectSelect").addEventListener("change", (event) => this.selectProject(event.target.value));
    byId("createProjectButton").addEventListener("click", () => this.openDialog("projectDialog"));
    document.addEventListener("click", (event) => this.click(event));
    document.addEventListener("submit", (event) => this.submit(event));
    document.addEventListener("change", (event) => this.change(event));
    document.addEventListener("input", (event) => this.input(event));
    document.addEventListener("keydown", (event) => this.keydown(event));
    document.addEventListener("scroll", (event) => { if (!event.target.closest?.(".generation-dropdown-options")) this.generation.closeDropdowns(); }, true);
    window.addEventListener("resize", () => this.generation.closeDropdowns());
    document.addEventListener("play", (event) => this.pauseOtherAudio(event.target), true);
    byId("scriptUploadFile").addEventListener("change", (event) => { byId("scriptUploadFileName").textContent = event.target.files[0]?.name || "选择台本文件"; });
    byId("smartScriptImportFiles").addEventListener("change", (event) => { byId("smartScriptImportFileName").textContent = event.target.files.length ? `${event.target.files.length} 个台本文件` : "选择一个或多个台本文件"; });
    byId("voiceFiles").addEventListener("change", (event) => { byId("voiceFileName").textContent = event.target.files.length ? `${event.target.files.length} 条参考录音` : "选择参考录音"; });
  }

  async boot(user) {
    this.state.user = user;
    byId("clientId").textContent = user.display_name || user.username;
    const isSystemAdmin = user.role === "system_admin";
    byId("identityRole").textContent = isSystemAdmin ? "系统管理员" : "项目成员";
    byId("userAvatar").textContent = first(user.display_name || user.username);
    byId("adminLink").hidden = !isSystemAdmin;
    try {
      const [config, projects] = await Promise.all([this.api.get("/api/config"), this.api.get("/api/projects")]);
      this.state.config = config;
      this.state.projects = projects.projects;
      this.renderProjectSelect();
      const restoredId = this.restoreProjectId();
      const projectId = this.state.projects.some((project) => project.id === restoredId)
        ? restoredId
        : this.state.projects[0]?.id || "";
      await this.selectProject(projectId, true);
    } catch (error) { this.toast(error.message, true); }
  }

  restoreProjectId() {
    try { return sessionStorage.getItem("voice-lab-workstation-project"); } catch (_) { return null; }
  }

  storeProjectId(id) {
    try { sessionStorage.setItem("voice-lab-workstation-project", id); } catch (_) { /* Storage can be unavailable. */ }
  }

  restorePosition() {
    try {
      const value = JSON.parse(sessionStorage.getItem(WORKSTATION_POSITION_KEY) || "{}");
      return value && typeof value === "object" ? value : {};
    } catch (_) { return {}; }
  }

  restoreProjectPosition(id) {
    const position = this.restorePosition().projects?.[id];
    return position && typeof position === "object" ? position : {};
  }

  restoreView() {
    const view = this.restorePosition().view;
    return WORKSTATION_VIEWS.has(view) ? view : "script";
  }

  storePosition() {
    const projectId = this.state.project?.id;
    if (!projectId) return;
    try {
      const stored = this.restorePosition();
      const projects = stored.projects && typeof stored.projects === "object" ? stored.projects : {};
      projects[projectId] = {
        ...(projects[projectId] || {}),
        scriptId: this.state.selectedScriptId,
        voiceId: this.state.selectedVoiceId,
        modelId: this.state.selectedModelId,
        generationConfigurations: this.state.generationConfigurations.map(({ voiceId, modelId, candidateCount }) => ({ voiceId, modelId, candidateCount })),
      };
      sessionStorage.setItem(WORKSTATION_POSITION_KEY, JSON.stringify({
        ...stored,
        view: this.state.currentView,
        projects,
      }));
    } catch (_) { /* Storage can be unavailable. */ }
  }

  async selectProject(id, initial = false) {
    const version = ++this.requestVersion;
    if (!id) { this.state.project = null; this.state.scripts = []; this.state.voices = []; this.state.jobs = []; this.state.textRuns = []; this.state.contextRevisions = { project: [], script: [] }; this.state.textGenerationDrafts = {}; this.state.textRunParentId = null; this.state.scriptImportBatch = null; this.state.scriptImportError = ""; this.state.scriptDetail = null; this.state.voiceDetail = null; this.state.jobDetails = {}; this.loadingVoice = null; this.loadingMembers = null; this.loadingJobIds.clear(); this.render(); return; }
    const previousProjectId = this.state.project?.id || null;
    const savedPosition = this.restoreProjectPosition(id);
    const restoredView = initial ? this.restoreView() : "script";
    try {
      const [projectResult, scriptsResult, voicesResult, jobsResult, importResult, textRunsResult, projectRevisionsResult] = await Promise.all([
        this.api.get(`/api/projects/${enc(id)}`),
        this.api.get(`/api/scripts?project_id=${enc(id)}`),
        this.api.get(`/api/voices?project_id=${enc(id)}`),
        this.api.get(`/api/jobs?limit=300&project_id=${enc(id)}`),
        this.api.get(`/api/projects/${enc(id)}/script-imports/pending`).catch((error) => {
          if (error.status !== 409) throw error;
          return { batch: null, error: error.message };
        }),
        this.api.get(`/api/projects/${enc(id)}/text-generation-runs?limit=200`),
        this.api.get(`/api/projects/${enc(id)}/context-revisions?limit=100`),
      ]);
      if (version !== this.requestVersion) return;
      this.state.project = projectResult.project;
      this.state.scripts = scriptsResult.scripts;
      this.state.voices = voicesResult.voices;
      this.state.jobs = jobsResult.jobs;
      this.state.textRuns = textRunsResult.runs || [];
      this.state.contextRevisions = { project: projectRevisionsResult.revisions || [], script: [] };
      for (const run of this.state.textRuns) {
        if (run.status === "queued" || run.status === "running") this.watchTextRun(run.id);
      }
      this.state.scriptImportBatch = importResult.batch;
      this.state.scriptImportError = importResult.error || "";
      // A project refresh must not keep a detail response from the previous snapshot.
      this.state.scriptDetail = null;
      this.loadingScript = null;
      this.loadingScriptPromise = null;
      this.loadingScriptToken += 1;
      if (previousProjectId !== id) {
        this.closeSettingsDrawer({ restoreFocus: false });
        this.state.scriptSearchQuery = "";
        this.state.voiceSearchQuery = "";
        this.state.assetRename = null;
        this.state.voiceDetail = null;
        this.state.jobDetails = {};
        this.state.generationConfigurations = [];
        this.state.projectPromptSuggestion = "";
        this.state.projectPromptDraft = null;
        this.state.scriptPromptSuggestion = "";
        this.state.scriptPromptDraft = null;
        this.state.generatedLines = [];
        this.state.generatedLinesScriptId = null;
        this.state.textGenerationDrafts = {};
        this.state.textRunParentId = null;
        this.state.contextRevisions.script = [];
        this.state.lineRewriteSuggestions = {};
        this.state.lineRewriteInstructions = {};
        this.state.scriptItemDrafts = {};
        this.state.pronunciationSuggestions = [];
        this.state.pronunciationSuggestionsScriptId = null;
        this.loadingVoice = null;
        this.loadingMembers = null;
        this.loadingJobIds.clear();
      }
      this.applyCompletedProjectTextRuns();
      const savedScriptId = previousProjectId === id ? this.state.selectedScriptId : savedPosition.scriptId;
      const savedVoiceId = previousProjectId === id ? this.state.selectedVoiceId : savedPosition.voiceId;
      const savedModelId = previousProjectId === id ? this.state.selectedModelId : savedPosition.modelId;
      this.state.selectedScriptId = this.state.scripts.some((item) => item.id === savedScriptId) ? savedScriptId : this.state.scripts[0]?.id || null;
      const usableVoices = this.state.voices.filter((item) => item.enabled_file_count);
      this.state.selectedVoiceId = usableVoices.some((item) => item.id === savedVoiceId) ? savedVoiceId : usableVoices[0]?.id || this.state.voices[0]?.id || null;
      const models = this.state.config.models.filter((model) => model.available === true);
      this.state.selectedModelId = models.some((model) => model.id === savedModelId) ? savedModelId : models[0]?.id || null;
      const generationConfigurations = previousProjectId === id
        ? this.state.generationConfigurations
        : savedPosition.generationConfigurations;
      this.generation.restore(generationConfigurations, usableVoices, models);
      this.storeProjectId(id);
      this.state.currentView = restoredView;
      this.renderProjectSelect();
      this.render();
      this.showView(restoredView);
      for (const job of this.state.jobs.filter((item) => item.script_id === this.state.selectedScriptId)) void this.loadJobDetail(job.id);
      byId("workspaceMain").scrollTop = 0;
      if (!initial) this.toast("已切换项目");
    } catch (error) { if (version === this.requestVersion) this.toast(error.message, true); }
  }

  isCurrentProjectRequest(projectId, version) {
    return version === this.requestVersion && this.state.project?.id === projectId;
  }

  isCurrentScriptRequest(projectId, scriptId, version) {
    return this.isCurrentProjectRequest(projectId, version) && this.state.selectedScriptId === scriptId;
  }

  isCurrentVoiceRequest(projectId, voiceId, version) {
    return this.isCurrentProjectRequest(projectId, version) && this.state.selectedVoiceId === voiceId;
  }

  renderProjectSelect() {
    const select = byId("projectSelect");
    select.innerHTML = this.state.projects.length ? this.state.projects.map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("") : '<option value="">暂无项目</option>';
    select.value = this.state.project?.id || "";
  }

  render() {
    const project = this.state.project;
    byId("emptyWorkspace").hidden = Boolean(project);
    byId("workspaceGrid").hidden = !project;
    const activeJobs = this.state.jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    byId("queueSummary").textContent = activeJobs ? `${activeJobs} 个处理中` : "空闲";
    byId("workerDot").className = this.state.jobs.some((job) => job.status === "running") ? "online" : "";
    if (!project) return;
    this.renderScript();
    this.renderVoice();
    this.renderProject();
    this.renderSettingsDrawer();
  }

  showView(view) {
    if (!WORKSTATION_VIEWS.has(view)) return;
    if (this.state.settingsDrawer && this.state.settingsDrawer !== view) this.closeSettingsDrawer({ restoreFocus: false });
    this.state.currentView = view;
    this.storePosition();
    document.querySelectorAll("[data-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; });
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    byId("workspaceMain").scrollTop = 0;
  }

  renderScript(options = {}) {
    const focus = this.captureScriptFocus();
    const selected = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const list = this.scriptAssetList();
    const detail = selected ? this.scriptDetail(selected) : '<div class="empty-panel"><div><strong>选择或导入一个台本</strong>上传后可编辑每行台词和发音。</div></div>';
    const content = byId("scriptContent");
    const listElement = content.querySelector(".asset-list");
    const detailElement = content.querySelector(".detail-panel");
    if (listElement && detailElement) {
      listElement.innerHTML = list;
      const count = content.querySelector(".asset-sidebar-header small");
      if (count) count.textContent = `${this.state.scripts.length} 个台本`;
      if (!options.listOnly) detailElement.innerHTML = detail;
    } else {
      content.innerHTML = `<div class="split-workspace asset-workspace"><aside class="asset-sidebar"><header class="asset-sidebar-header"><div><h2>台本</h2><small>${this.state.scripts.length} 个台本</small></div><button class="button button-quiet button-small" type="button" data-action="open-script">导入台本</button></header><label class="asset-search" for="scriptSearch"><input id="scriptSearch" type="search" value="${escapeHtml(this.state.scriptSearchQuery)}" autocomplete="off" placeholder="搜索台本" aria-label="搜索台本"></label><div class="asset-list">${list}</div></aside><section class="detail-panel">${detail}</section></div>`;
    }
    this.restoreScriptFocus(focus);
    if (selected) void this.loadScriptDetail(selected.id);
  }

  scriptAssetList() {
    const selected = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const query = String(this.state.scriptSearchQuery || "").trim().toLowerCase();
    const scripts = query ? this.state.scripts.filter((script) => String(script.search_text || `${script.name} ${script.original_name}`).toLowerCase().includes(query)) : this.state.scripts;
    return scripts.map((script) => {
      if (this.state.assetRename?.kind === "script" && this.state.assetRename.id === script.id) return this.assetRenameForm("script", script);
      return `<button class="asset-row${script.id === selected?.id ? " active" : ""}" type="button" data-action="select-script" data-id="${escapeHtml(script.id)}" aria-current="${script.id === selected?.id}"><span class="asset-icon">T</span><span class="asset-copy"><strong>${escapeHtml(script.name)}</strong><small>${script.item_count} 行 · ${formatDate(script.created_at)}</small></span><span class="asset-status">台词</span></button>`;
    }).join("") || (query && this.state.scripts.length ? '<p class="empty-list">没有匹配的台本</p>' : '<p class="empty-list">尚未导入台本</p>');
  }

  assetRenameForm(kind, asset) {
    const selectedId = kind === "script" ? this.state.selectedScriptId : this.state.selectedVoiceId;
    const icon = kind === "script" ? "T" : "♪";
    const label = kind === "script" ? "台本" : "声音";
    return `<form id="assetRenameForm" class="asset-row asset-rename-row${asset.id === selectedId ? " active" : ""}" data-asset-rename="${kind}" data-id="${escapeHtml(asset.id)}"><span class="asset-icon">${icon}</span><label class="asset-rename-field"><input name="name" value="${escapeHtml(asset.name)}" maxlength="80" autocomplete="off" required aria-label="重命名${label}"><small>Enter 保存 · Esc 取消</small></label><button type="submit" hidden>保存</button></form>`;
  }

  captureScriptFocus() {
    const active = document.activeElement;
    if (active?.id === "scriptSearch") {
      return { type: "search", start: active.selectionStart, end: active.selectionEnd };
    }
    const rename = active?.closest?.('[data-asset-rename="script"]');
    if (rename) return { type: "rename", id: rename.dataset.id, start: active.selectionStart, end: active.selectionEnd };
    const row = active?.closest?.('[data-action="select-script"]');
    return row ? { type: "script", id: row.dataset.id } : null;
  }

  restoreScriptFocus(focus) {
    if (!focus) return;
    if (focus.type === "rename") {
      const form = [...document.querySelectorAll('[data-asset-rename="script"]')].find((item) => item.dataset.id === focus.id);
      const input = form?.querySelector('input[name="name"]');
      input?.focus({ preventScroll: true });
      if (input && Number.isInteger(focus.start)) input.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
      return;
    }
    if (focus.type === "script") {
      const row = [...document.querySelectorAll('[data-action="select-script"]')].find((item) => item.dataset.id === focus.id);
      row?.focus({ preventScroll: true });
      return;
    }
    const search = document.querySelector("#scriptSearch");
    if (!search) return;
    if (document.activeElement !== search) {
      search.focus({ preventScroll: true });
      if (Number.isInteger(focus.start)) search.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
    }
  }

  loadScriptDetail(id) {
    if (this.state.scriptDetail?.id === id) return Promise.resolve(this.state.scriptDetail);
    if (this.loadingScript === id && this.loadingScriptPromise) return this.loadingScriptPromise;
    this.loadingScript = id;
    const token = ++this.loadingScriptToken;
    const version = this.requestVersion;
    let promise;
    promise = (async () => {
      try {
        const [scriptResult, runsResult, revisionsResult] = await Promise.all([
          this.api.get(`/api/scripts/${enc(id)}`),
          this.api.get(`/api/projects/${enc(this.state.project?.id || "")}/text-generation-runs?script_id=${enc(id)}&limit=100`),
          this.api.get(`/api/scripts/${enc(id)}/context-revisions?limit=100`),
        ]);
        if (version === this.requestVersion && token === this.loadingScriptToken && this.state.selectedScriptId === id) {
          this.state.scriptDetail = scriptResult.script;
          this.state.contextRevisions.script = revisionsResult.revisions || [];
          for (const run of runsResult.runs || []) this.upsertTextRun(run);
          this.applyCompletedTextRuns(id);
          this.renderScript();
          this.renderOpenSettingsDrawer("script");
        }
        return this.state.scriptDetail?.id === id ? this.state.scriptDetail : null;
      } catch (error) {
        if (version === this.requestVersion) this.toast(error.message, true);
        return null;
      } finally {
        if (token === this.loadingScriptToken) this.loadingScript = null;
        if (this.loadingScriptPromise === promise) this.loadingScriptPromise = null;
      }
    })();
    this.loadingScriptPromise = promise;
    return promise;
  }

  scriptDetail(script) {
    const detail = this.state.scriptDetail?.id === script.id ? this.state.scriptDetail : null;
    if (!detail) return '<div class="empty-panel">正在读取台本...</div>';
    const models = this.state.config.models.filter((model) => model.available === true);
    const voices = this.state.voices.filter((voice) => voice.enabled_file_count);
    const canGenerate = Boolean(voices.length && models.length && detail.items.length);
    const canGeneratePronunciations = Boolean(detail.items.length && this.state.config.text_model?.configured === true);
    const generationJobs = this.generationJobsForScript(detail.id);
    const selections = detail.selections || [];
    const selectedSequences = new Set(selections.map((row) => Number(row.sequence)));
    const selectedCount = detail.items.filter((item, index) => selectedSequences.has(Number(item.order || index + 1))).length;
    const lines = detail.items.map((item, index) => this.scriptLine(detail.id, item, index, canGenerate)).join("");
    const generationStatus = `${canGenerate ? `${detail.items.length} 行可生成 · 支持逐条配置` : "需要先添加可用声音和模型"}${generationJobs.length ? ` · ${generationJobs.length} 条生成记录` : ""} · 已采纳 ${selectedCount}/${detail.items.length}`;
    const pronunciationStatus = canGeneratePronunciations ? "依据角色台词特性生成全部发音候选" : "需要系统管理员先启用台词文本模型";
    return `<div class="detail-inner"><header class="detail-header asset-detail-header"><div><span class="eyebrow">SCRIPT DETAIL</span><h2>${escapeHtml(detail.name)}</h2><p>${detail.item_count} 行台词 · ${escapeHtml(detail.original_name)}</p></div><div class="detail-actions asset-detail-actions"><div class="asset-detail-generation"><button class="button button-quiet button-small" type="button" data-action="generate-pronunciations"${canGeneratePronunciations ? "" : " disabled"} aria-label="生成全部发音候选" title="${escapeHtml(pronunciationStatus)}">生成全部发音</button><button class="button button-primary button-small" type="button" data-action="generate-all"${canGenerate ? "" : " disabled"} aria-label="生成全部音频" title="生成全部音频">全部生成</button><small class="generation-help" title="${escapeHtml(generationStatus)}">${escapeHtml(generationStatus)}</small></div><button class="button button-quiet button-small asset-settings-trigger" type="button" data-action="open-settings-drawer" data-kind="script" aria-haspopup="dialog"><span aria-hidden="true">⚙</span><span>设置</span></button></div></header><form id="scriptItemsForm" class="script-editor"><div class="editor-toolbar"><div><h3>台词与发音</h3><p>编辑内容、生成音频并试听采纳；修改后点击右下角保存。</p></div></div>${this.pronunciationSuggestionsPanel(detail.id)}<div class="script-lines">${lines}</div><button class="button button-primary script-save-floating" type="submit" aria-label="保存全部台词与发音">保存台词</button></form></div>`;
  }

  scriptSettingsDrawer(detail) {
    const generationJobs = this.generationJobsForScript(detail.id);
    const deletableJobs = generationJobs.filter((job) => !["queued", "running"].includes(job.status));
    const selectedSequences = new Set((detail.selections || []).map((row) => Number(row.sequence)));
    const selectedCount = detail.items.filter((item, index) => selectedSequences.has(Number(item.order || index + 1))).length;
    const hasActiveJobs = generationJobs.some((job) => ["queued", "running"].includes(job.status));
    const canExportAccepted = !hasActiveJobs && selectedCount === detail.items.length && detail.items.length > 0;
    const canExportAll = !hasActiveJobs && generationJobs.some((job) => job.status === "completed");
    const pendingLines = this.state.generatedLinesScriptId === detail.id ? this.state.generatedLines : [];
    const textDraft = this.textGenerationDraft(detail.id);
    const exportAccepted = canExportAccepted
      ? '<button class="button button-quiet" type="button" data-action="export-script-audio" data-scope="accepted">导出已采纳音频</button>'
      : `<button class="button button-quiet" type="button" disabled>已采纳 ${selectedCount}/${detail.items.length}</button>`;
    const exportAll = canExportAll
      ? '<button class="button button-quiet" type="button" data-action="export-script-audio" data-scope="all">导出全部音频历史</button>'
      : '<button class="button button-quiet" type="button" disabled>暂无可导出历史</button>';
    const textHistory = this.renderTextRunHistory(detail.id);
    const contextHistory = this.renderContextRevisionHistory("script", detail.id);
    return `<div class="settings-drawer-shell"><header class="settings-drawer-header"><div><span class="eyebrow">台本</span><h2 id="assetSettingsTitle">台本设置</h2><p>重命名、角色配置与资产管理</p></div><button class="icon-button" type="button" data-action="close-settings-drawer" title="关闭设置" aria-label="关闭设置">×</button></header><div class="settings-drawer-body"><form id="scriptSettingsForm" class="settings-section"><div class="settings-section-heading"><h3>台本信息</h3><p>在设置中修改台本名称。</p></div><label class="field"><span>台本 / 角色名称</span><input name="name" value="${escapeHtml(detail.name)}" maxlength="80" required></label><div class="form-actions"><button class="button button-primary" type="submit">保存名称</button></div></form><form id="textGenerationForm" class="settings-section"><div class="settings-section-heading"><h3>AI 生成台词</h3><p>长期角色特性会用于新台词和单行修改。</p></div><label class="field"><span>角色台词特性</span><textarea name="prompt" maxlength="12000" placeholder="例如：说话克制、句子短，不主动解释情绪；遇到质疑时先停顿，再用事实回应。">${escapeHtml(this.state.scriptPromptDraft ?? detail.prompt ?? "")}</textarea><small>生成前自动保存；AI 候选不会直接覆盖现有台词。</small></label><div class="character-context-actions"><button class="button button-quiet" type="button" data-action="suggest-script-prompt">让 AI 完善角色特性</button><button class="button button-quiet" type="button" data-action="save-character-context">保存角色特性</button></div>${this.textRunParentNotice("script", detail.id)}${this.promptSuggestion(this.state.scriptPromptSuggestion, "script")}${contextHistory}<label class="field"><span>本次要求</span><textarea name="instruction" maxlength="4000" placeholder="例如：生成一组首次见面时的短句，克制但带有警惕感。">${escapeHtml(textDraft.instruction ?? "")}</textarea></label><div class="text-generation-controls"><label class="field"><span>句数</span><input name="line_count" type="number" min="1" max="100" value="${escapeHtml(textDraft.line_count ?? "5")}"></label><button class="button button-primary" type="submit">生成台词候选</button></div>${pendingLines.length ? this.generatedLinesPanel(pendingLines) : ""}${textHistory}</form><section class="settings-section settings-asset-actions"><div class="settings-section-heading"><h3>导出与清理</h3><p>这些操作不会改变主编辑区中的台词内容。</p></div><div class="settings-action-grid"><a class="button button-quiet" href="/api/scripts/${enc(detail.id)}/export">导出台词 CSV</a>${exportAccepted}${exportAll}<button class="button button-danger" type="button" data-action="delete-all-generations"${deletableJobs.length ? "" : " disabled"}>删除全部生成历史</button></div><button class="button button-danger settings-delete-asset" type="button" data-action="delete-script">删除当前台本</button></section></div></div>`;
  }

  promptSuggestion(suggestion, scope) {
    return `<div id="promptSuggestion-${scope}" data-prompt-suggestion="${scope}">${suggestion ? `<section class="prompt-suggestion"><div><strong>AI 建议草稿</strong><small>当前内容不会自动被替换。</small></div><pre>${escapeHtml(suggestion)}</pre><button class="button button-quiet button-small" type="button" data-action="adopt-${scope}-prompt">采用建议（仍需保存）</button></section>` : ""}</div>`;
  }

  generatedLinesPanel(lines) {
    return `<section class="generated-lines-panel"><header><strong>台词候选</strong><small>${lines.length} 句 · 尚未加入台本</small></header><ol>${lines.map((line) => `<li>${escapeHtml(line.text)}</li>`).join("")}</ol><button class="button button-quiet button-small" type="button" data-action="adopt-generated-lines">确认并加入编辑器</button></section>`;
  }

  scriptLine(scriptId, item, index, canGenerate) {
    const sequence = Number(item.order || index + 1);
    const draft = this.state.scriptItemDrafts[scriptId]?.[sequence];
    const text = draft?.text ?? item.text;
    const pronunciation = draft?.pronunciation ?? item.pronunciation;
    const rewriteInstruction = draft?.rewrite_instruction ?? item.rewrite_instruction ?? this.state.lineRewriteInstructions[this.lineRewriteKey(scriptId, sequence)] ?? "";
    return `<div class="script-line" data-script-item data-line-number="${sequence}"><div class="line-heading"><span class="line-number">${String(sequence).padStart(2, "0")}</span><div class="line-actions"><button class="button button-quiet button-small" type="button" data-action="generate-line" data-line-number="${sequence}"${canGenerate ? "" : " disabled"}>生成音频</button></div></div><div class="line-fields"><label><span>台词</span><textarea name="text" maxlength="2000">${escapeHtml(text)}</textarea></label><label><span>发音</span><textarea name="pronunciation" maxlength="2000">${escapeHtml(pronunciation)}</textarea></label></div><div class="line-rewrite-controls"><label><span>AI 单行修改要求</span><input name="rewrite_instruction" maxlength="4000" value="${escapeHtml(rewriteInstruction)}" placeholder="例如：更克制、缩短到 20 字以内，不改变事实。"></label><button class="button button-quiet button-small" type="button" data-action="rewrite-line" data-line-number="${sequence}">生成修改候选</button></div><div class="line-rewrite-result">${this.lineRewriteSuggestion(scriptId, sequence)}</div>${this.lineResult(scriptId, sequence)}</div>`;
  }

  lineRewriteKey(scriptId, sequence) {
    return `${scriptId}:${sequence}`;
  }

  pronunciationSuggestionsPanel(scriptId) {
    const lines = this.state.pronunciationSuggestionsScriptId === scriptId ? this.state.pronunciationSuggestions : [];
    if (!lines.length) return "";
    return `<section class="pronunciation-suggestions"><header><strong>批量发音候选</strong><small>${lines.length} 行 · 尚未替换当前发音</small></header><ol>${lines.map((line) => `<li><span>${String(line.sequence).padStart(2, "0")}</span><p>${escapeHtml(line.pronunciation)}</p></li>`).join("")}</ol><div class="pronunciation-suggestion-actions"><button class="button button-primary button-small" type="button" data-action="adopt-all-pronunciations">全部采用</button><button class="button button-quiet button-small" type="button" data-action="discard-all-pronunciations">放弃</button></div></section>`;
  }

  lineRewriteSuggestion(scriptId, sequence) {
    const suggestion = this.state.lineRewriteSuggestions[this.lineRewriteKey(scriptId, sequence)];
    if (!suggestion) return "";
    return `<section class="line-rewrite-suggestion"><header><strong>AI 修改候选</strong><small>尚未替换当前行</small></header><div class="line-rewrite-preview"><div><span>台词</span><p>${escapeHtml(suggestion.text)}</p></div><div><span>发音</span><p>${escapeHtml(suggestion.pronunciation)}</p></div></div><div class="line-rewrite-actions"><button class="button button-primary button-small" type="button" data-action="adopt-line-rewrite" data-line-number="${sequence}">采用修改</button><button class="button button-quiet button-small" type="button" data-action="discard-line-rewrite" data-line-number="${sequence}">放弃</button></div></section>`;
  }

  lineResult(scriptId, sequence) {
    const jobs = this.generationJobsForScript(scriptId);
    const selection = (this.state.scriptDetail?.selections || []).find((row) => Number(row.sequence) === sequence);
    const entries = [];
    for (const job of jobs) {
      const detail = this.state.jobDetails[job.id];
      const item = detail?.items?.find((entry) => Number(entry.sequence) === sequence);
      if (item || (!detail && Number(job.total_items) > 1)) entries.push({ job, item, batchKey: this.historyBatchKey(job) });
    }
    if (!entries.length) return "";
    const latestEntry = entries.reduce((latest, entry) => this.historyTimestamp(entry.job) > this.historyTimestamp(latest.job) ? entry : latest, entries[0]);
    const latest = entries.filter((entry) => entry.batchKey === latestEntry.batchKey);
    const archived = entries.filter((entry) => entry.batchKey !== latestEntry.batchKey);
    const renderEntries = (items) => items.map(({ job, item }) => this.renderLineHistory(job, item, selection)).join("");
    const archive = archived.length
      ? `<details class="line-history-archive"><summary><span>历史批次</span><small>${archived.length} 条任务，默认收起</small></summary><div class="line-history-grid">${renderEntries(archived)}</div></details>`
      : "";
    return `<section class="line-history" aria-label="第 ${sequence} 行生成历史"><div class="line-history-heading"><span>最新批次</span><small>${latest.length} 条任务 · ${this.historyBatchStatus(latest)}</small></div><div class="line-history-grid">${renderEntries(latest)}</div>${archive}</section>`;
  }

  historyTimestamp(job) {
    const timestamp = Date.parse(String(job?.submitted_at || ""));
    return Number.isFinite(timestamp) ? timestamp : 0;
  }

  historyBatchKey(job) {
    const timestamp = this.historyTimestamp(job);
    return timestamp ? `time:${Math.floor(timestamp / 1000)}` : `job:${job?.id || ""}`;
  }

  historyBatchStatus(entries) {
    const statuses = new Set(entries.map(({ job, item }) => item?.status || job.status));
    if (statuses.has("running")) return "生成中";
    if (statuses.has("queued")) return "排队中";
    if (statuses.has("completed") && statuses.has("failed")) return "部分完成";
    if (statuses.has("completed")) return "已完成";
    if (statuses.has("failed")) return "失败";
    return "待确认";
  }

  renderLineHistory(job, item, selection) {
    const status = item?.status || job.status;
    const canDelete = Boolean(item?.id) && !["queued", "running"].includes(status) && !["queued", "running"].includes(job.status);
    const scope = Number(job.total_items) === 1 ? "单条" : "全部";
    const deleteButton = canDelete ? `<button class="icon-button line-history-delete" type="button" data-action="delete-generation" data-job-id="${escapeHtml(job.id)}" data-item-id="${escapeHtml(item.id)}" title="删除这条生成记录" aria-label="删除这条生成记录"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>` : "";
    const candidateCards = item?.candidates?.length
      ? item.candidates.map((candidate) => this.renderCandidateCard(job, item, candidate, selection)).join("")
      : `<small class="line-history-message">${escapeHtml(item?.error || (status === "completed" ? "音频详情暂不可用" : "音频生成后会显示在这里"))}</small>`;
    const selectedHere = selection && selection.job_id === job.id;
    const selectionNote = selectedHere ? `<span class="line-selection-note">已采纳 · ${escapeHtml(selection.selected_by_name || "项目成员")}</span>` : "";
    return `<article class="line-history-row"><div class="line-history-main"><div class="line-history-top"><span class="status-pill ${status === "failed" ? "danger" : status !== "completed" ? "warning" : ""}">${escapeHtml(statusLabel(status))}</span><span class="line-history-scope">${scope}</span>${selectionNote}<time>${escapeHtml(formatDate(job.submitted_at))}</time>${deleteButton}</div><div class="line-history-meta"><span>声音：${escapeHtml(job.voice_name || job.voice_id || "未指定")}</span><span>模型：${escapeHtml(this.modelLabel(job.model_id))}</span><span class="line-history-id">任务：${escapeHtml(job.id)}</span></div><div class="line-history-candidates">${candidateCards}</div></div></article>`;
  }

  renderCandidateCard(job, item, candidate, selection) {
    const selected = Boolean(selection && selection.job_id === job.id && selection.item_id === item.id && selection.candidate_id === candidate.id);
    const action = selected
      ? `<button class="button button-quiet button-small" type="button" data-action="clear-generation-selection" data-sequence="${escapeHtml(item.sequence)}">取消采纳</button>`
      : candidate.status === "completed"
        ? `<button class="button button-primary button-small" type="button" data-action="select-generation" data-job-id="${escapeHtml(job.id)}" data-item-id="${escapeHtml(item.id)}" data-candidate-id="${escapeHtml(candidate.id)}" data-sequence="${escapeHtml(item.sequence)}">采纳</button>`
        : "";
    const audioUrl = safeResourceUrl(candidate.audio_url);
    const audio = audioUrl ? `<audio controls preload="none" src="${escapeHtml(audioUrl)}"></audio>` : `<small class="candidate-pending">${escapeHtml(candidate.error || "候选生成后会显示在这里")}</small>`;
    const badge = selected ? "<span>已采纳</span>" : `<span>${escapeHtml(statusLabel(candidate.status))}</span>`;
    return `<article class="line-history-candidate${selected ? " selected" : ""}"><div class="candidate-top"><strong>${escapeHtml(candidate.name || `候选 ${candidate.ordinal}`)}</strong>${badge}</div><div class="candidate-media">${audio}${action}</div></article>`;
  }

  modelLabel(modelId) {
    const model = this.state.config.models.find((item) => item.id === modelId);
    return model?.label || modelId || "未指定";
  }

  generationJobsForScript(scriptId) {
    return this.state.jobs.filter((job) => job.script_id === scriptId);
  }

  renderVoice(options = {}) {
    const focus = this.captureVoiceFocus();
    const selected = this.state.voices.find((item) => item.id === this.state.selectedVoiceId);
    const list = this.voiceAssetList();
    const detail = selected ? this.voiceDetail(selected) : '<div class="empty-panel"><div><strong>选择或创建一个声音</strong>声音库中的可用录音可以在台本页直接使用。</div></div>';
    const content = byId("voiceContent");
    const listElement = content.querySelector(".asset-list");
    const detailElement = content.querySelector(".detail-panel");
    if (listElement && detailElement) {
      listElement.innerHTML = list;
      const count = content.querySelector(".asset-sidebar-header small");
      if (count) count.textContent = `${this.state.voices.length} 个声音`;
      if (!options.listOnly) detailElement.innerHTML = detail;
    } else {
      content.innerHTML = `<div class="split-workspace asset-workspace"><aside class="asset-sidebar"><header class="asset-sidebar-header"><div><h2>声音</h2><small>${this.state.voices.length} 个声音</small></div><button class="button button-quiet button-small" type="button" data-action="open-voice">添加声音</button></header><label class="asset-search" for="voiceSearch"><input id="voiceSearch" type="search" value="${escapeHtml(this.state.voiceSearchQuery)}" autocomplete="off" placeholder="搜索声音" aria-label="搜索声音"></label><div class="asset-list">${list}</div></aside><section class="detail-panel">${detail}</section></div>`;
    }
    this.restoreVoiceFocus(focus);
    if (selected) void this.loadVoiceDetail(selected.id);
  }

  voiceAssetList() {
    const selected = this.state.voices.find((item) => item.id === this.state.selectedVoiceId);
    const query = String(this.state.voiceSearchQuery || "").trim().toLowerCase();
    const voices = query ? this.state.voices.filter((voice) => String(voice.search_text || `${voice.name} ${voice.notes}`).toLowerCase().includes(query)) : this.state.voices;
    return voices.map((voice) => {
      if (this.state.assetRename?.kind === "voice" && this.state.assetRename.id === voice.id) return this.assetRenameForm("voice", voice);
      return `<button class="asset-row${voice.id === selected?.id ? " active" : ""}" type="button" data-action="select-voice" data-id="${escapeHtml(voice.id)}" aria-current="${voice.id === selected?.id}"><span class="asset-icon">♪</span><span class="asset-copy"><strong>${escapeHtml(voice.name)}</strong><small>${voice.enabled_file_count}/${voice.file_count} 条录音</small></span><span class="asset-status ${voice.enabled_file_count ? "ready" : "pending"}">${voice.enabled_file_count ? "可用" : "待录入"}</span></button>`;
    }).join("") || (query && this.state.voices.length ? '<p class="empty-list">没有匹配的声音</p>' : '<p class="empty-list">尚未创建声音</p>');
  }

  captureVoiceFocus() {
    const active = document.activeElement;
    if (active?.id === "voiceSearch") return { type: "search", start: active.selectionStart, end: active.selectionEnd };
    const rename = active?.closest?.('[data-asset-rename="voice"]');
    if (rename) return { type: "rename", id: rename.dataset.id, start: active.selectionStart, end: active.selectionEnd };
    const row = active?.closest?.('[data-action="select-voice"]');
    return row ? { type: "voice", id: row.dataset.id } : null;
  }

  restoreVoiceFocus(focus) {
    if (!focus) return;
    if (focus.type === "rename") {
      const form = [...document.querySelectorAll('[data-asset-rename="voice"]')].find((item) => item.dataset.id === focus.id);
      const input = form?.querySelector('input[name="name"]');
      input?.focus({ preventScroll: true });
      if (input && Number.isInteger(focus.start)) input.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
      return;
    }
    if (focus.type === "voice") {
      const row = [...document.querySelectorAll('[data-action="select-voice"]')].find((item) => item.dataset.id === focus.id);
      row?.focus({ preventScroll: true });
      return;
    }
    const search = document.querySelector("#voiceSearch");
    if (!search) return;
    if (document.activeElement !== search) {
      search.focus({ preventScroll: true });
      if (Number.isInteger(focus.start)) search.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
    }
  }

  async loadVoiceDetail(id) {
    if (this.state.voiceDetail?.id === id || this.loadingVoice === id) return;
    this.loadingVoice = id;
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    const token = ++this.loadingVoiceToken;
    try {
      const { voice } = await this.api.get(`/api/voices/${enc(id)}`);
      if (this.isCurrentProjectRequest(projectId, version) && token === this.loadingVoiceToken && this.state.selectedVoiceId === id) {
        this.state.voiceDetail = voice;
        this.renderVoice();
        this.renderOpenSettingsDrawer("voice");
      }
    } catch (error) {
      if (this.isCurrentProjectRequest(projectId, version)) this.toast(error.message, true);
    } finally {
      if (token === this.loadingVoiceToken) this.loadingVoice = null;
    }
  }

  voiceDetail(voice) {
    const detail = this.state.voiceDetail?.id === voice.id ? this.state.voiceDetail : null;
    if (!detail) return '<div class="empty-panel">正在读取声音...</div>';
    const files = detail.files.map((file) => {
      const audioUrl = safeResourceUrl(file.audio_url);
      return `<div class="voice-file-row voice-playback-row"><div class="voice-file-name"><strong>${escapeHtml(file.original_name)}</strong><small>${formatBytes(file.size_bytes)} · ${file.quality?.grade || "待检测"} · ${file.enabled ? "已启用" : "未启用"}</small></div>${audioUrl ? `<audio controls preload="none" src="${escapeHtml(audioUrl)}"></audio>` : '<small class="candidate-pending">音频地址不可用</small>'}</div>`;
    }).join("") || '<p class="empty-list">没有参考录音</p>';
    return `<div class="detail-inner"><header class="detail-header asset-detail-header"><div><span class="eyebrow">VOICE DETAIL</span><h2>${escapeHtml(detail.name)}</h2><p>${detail.enabled_file_count}/${detail.file_count} 条启用录音 · ${formatBytes(detail.size_bytes)}</p></div><button class="button button-quiet button-small asset-settings-trigger" type="button" data-action="open-settings-drawer" data-kind="voice" aria-haspopup="dialog"><span aria-hidden="true">⚙</span><span>设置</span></button></header><section class="voice-playback-section work-section"><div class="editor-toolbar"><div><h3>参考录音</h3><p>点击即可试听；录音启用状态和情绪在右侧设置中管理。</p></div></div><div class="voice-files">${files}</div></section></div>`;
  }

  voiceSettingsDrawer(detail) {
    const files = this.voiceSettingsFileRows(detail);
    return `<div class="settings-drawer-shell"><header class="settings-drawer-header"><div><span class="eyebrow">声音</span><h2 id="assetSettingsTitle">声音设置</h2><p>重命名、说明与参考录音管理</p></div><button class="icon-button" type="button" data-action="close-settings-drawer" title="关闭设置" aria-label="关闭设置">×</button></header><div class="settings-drawer-body"><form id="voiceSettingsForm" class="settings-section"><div class="settings-section-heading"><h3>声音信息</h3><p>在设置中修改声音名称和说明。</p></div><label class="field"><span>声音名称</span><input name="name" value="${escapeHtml(detail.name)}" maxlength="80" required></label><label class="field"><span>说明</span><textarea name="notes" maxlength="500" placeholder="录音者、音色或使用场景">${escapeHtml(detail.notes)}</textarea></label><div class="form-actions"><button class="button button-primary" type="submit">保存声音信息</button></div></form><section class="settings-section"><div class="settings-section-heading"><h3>录音配置</h3><p>启用的录音会作为生成参考。</p></div><div class="voice-settings-files">${files}</div></section><form id="voiceAppendForm" class="settings-section"><div class="settings-section-heading"><h3>追加录音</h3><p>支持一次选择多条参考音频。</p></div><label class="drawer-file-picker"><span>选择参考录音</span><input name="files" type="file" accept="audio/*" multiple required></label><div class="form-actions"><button class="button button-primary" type="submit">追加录音</button></div></form></div></div>`;
  }

  voiceSettingsFileRows(detail) {
    const emotions = this.state.config.reference_emotions || [];
    return detail.files.map((file) => `<div class="voice-settings-file-row"><div class="voice-file-name"><strong>${escapeHtml(file.original_name)}</strong><small>${formatBytes(file.size_bytes)} · ${file.quality?.grade || "待检测"}</small></div><div class="voice-file-controls"><select data-action="file-emotion" data-file-id="${escapeHtml(file.id)}" aria-label="${escapeHtml(file.original_name)} 的参考情绪">${emotions.map((emotion) => `<option value="${escapeHtml(emotion.id)}"${emotion.id === file.emotion_tag ? " selected" : ""}>${escapeHtml(emotion.label)}</option>`).join("")}</select><label class="switch"><input type="checkbox" data-action="file-enabled" data-file-id="${escapeHtml(file.id)}"${file.enabled ? " checked" : ""}>启用</label></div></div>`).join("") || '<p class="empty-list">没有参考录音</p>';
  }

  renderVoiceSettingsFiles() {
    const detail = this.state.voiceDetail?.id === this.state.selectedVoiceId ? this.state.voiceDetail : null;
    const target = byId("assetSettingsContent")?.querySelector(".voice-settings-files");
    if (!detail || !target) return;
    const active = document.activeElement;
    const fileId = active?.dataset?.fileId;
    const action = active?.dataset?.action;
    target.innerHTML = this.voiceSettingsFileRows(detail);
    if (!fileId || !action) return;
    const restored = [...target.querySelectorAll(`[data-action="${action}"]`)].find((element) => element.dataset.fileId === fileId);
    restored?.focus({ preventScroll: true });
  }

  async loadJobDetail(id, force = false) {
    const summary = this.state.jobs.find((job) => job.id === id);
    const cached = this.state.jobDetails[id];
    if (!force && cached && (!summary || cached.status === summary.status)) return;
    if (this.loadingJobIds.has(id)) return;
    this.loadingJobIds.add(id);
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    try {
      const { job } = await this.api.get(`/api/jobs/${enc(id)}`);
      if (this.isCurrentProjectRequest(projectId, version) && job.project_id === projectId) {
        this.state.jobDetails[id] = job;
        if (this.state.selectedScriptId === job.script_id && !document.activeElement?.closest?.("form")) this.renderScript();
      }
    } catch (error) {
      if (this.isCurrentProjectRequest(projectId, version)) this.toast(error.message, true);
    } finally {
      this.loadingJobIds.delete(id);
    }
  }

  upsertTextRun(run) {
    const index = this.state.textRuns.findIndex((item) => item.id === run.id);
    if (index >= 0) this.state.textRuns[index] = { ...this.state.textRuns[index], ...run };
    else this.state.textRuns.unshift(run);
    this.state.textRuns.sort((left, right) => this.textRunTimestamp(right) - this.textRunTimestamp(left));
  }

  invalidateTextRunDraft(run) {
    if (!run || run.status === "completed") return;
    const scriptId = String(run.script_id || "");
    if (!scriptId) {
      if (run.kind === "prompt_suggestion") this.state.projectPromptSuggestion = "";
      return;
    }
    if (run.kind === "lines" && this.state.generatedLinesScriptId === scriptId) {
      this.state.generatedLines = [];
      this.state.generatedLinesScriptId = null;
    }
    if (run.kind === "pronunciations" && this.state.pronunciationSuggestionsScriptId === scriptId) {
      this.state.pronunciationSuggestions = [];
      this.state.pronunciationSuggestionsScriptId = null;
    }
    if (run.kind === "rewrite_line") {
      const sequence = Number(run.input?.sequence);
      if (sequence) delete this.state.lineRewriteSuggestions[this.lineRewriteKey(scriptId, sequence)];
    }
    if (run.kind === "prompt_suggestion" && this.state.selectedScriptId === scriptId) {
      this.state.scriptPromptSuggestion = "";
    }
  }

  textRunTimestamp(run) {
    const value = Date.parse(String(run?.updated_at || run?.created_at || ""));
    return Number.isFinite(value) ? value : 0;
  }

  textGenerationDraft(scriptId = this.state.selectedScriptId) {
    const draft = scriptId ? this.state.textGenerationDrafts[scriptId] : null;
    return draft && typeof draft === "object" ? draft : {};
  }

  captureTextGenerationDraft(form = byId("textGenerationForm")) {
    const scriptId = this.state.selectedScriptId;
    if (!scriptId || !form) return;
    this.state.textGenerationDrafts[scriptId] = {
      ...this.textGenerationDraft(scriptId),
      instruction: form.instruction?.value ?? "",
      line_count: form.line_count?.value ?? "5",
    };
  }

  textRunFor(runId) {
    return this.state.textRuns.find((run) => run.id === runId) || null;
  }

  textRunParentNotice(scope, scriptId = null) {
    const run = this.textRunParent(scope, scriptId);
    if (!run) return "";
    return `<div class="text-run-parent"><span>将基于“${escapeHtml(this.textRunLabel(run.kind))}”继续生成</span><small>${escapeHtml(this.textRunPreview(run).slice(0, 180))}</small><button class="button button-quiet button-small" type="button" data-action="clear-text-run-parent">取消继承</button></div>`;
  }

  textRunParent(scope, scriptId = null) {
    const run = this.textRunFor(this.state.textRunParentId);
    if (!run || run.project_id !== this.state.project?.id) return null;
    if (scope === "project" && !run.script_id) return run;
    if (scope === "script" && run.script_id === scriptId) return run;
    return null;
  }

  textRunMatchesCurrentScriptVersion(run, scriptId) {
    if (!run || run.script_id !== scriptId) return false;
    const snapshot = Number(run.context?.script_version);
    const current = Number(this.state.scriptDetail?.version);
    return !Number.isInteger(snapshot) || !Number.isInteger(current) || snapshot === current;
  }

  applyCompletedTextRuns(scriptId) {
    const runs = this.state.textRuns.filter((run) => run.script_id === scriptId);
    const latest = new Map();
    for (const run of runs) {
      const key = run.kind === "rewrite_line" ? `${run.kind}:${run.input?.sequence || ""}` : run.kind;
      if (!latest.has(key)) latest.set(key, run);
    }
    const completed = (run) => {
      if (!run || run.status !== "completed") return false;
      if (["lines", "rewrite_line", "pronunciations"].includes(run.kind)) {
        return this.textRunMatchesCurrentScriptVersion(run, scriptId);
      }
      return true;
    };
    const latestLines = latest.get("lines");
    const linesRun = completed(latestLines) ? latestLines : null;
    if (!linesRun && this.state.generatedLinesScriptId === scriptId) {
      this.state.generatedLines = [];
      this.state.generatedLinesScriptId = null;
    }
    if (linesRun?.result?.lines?.length) {
      this.state.generatedLines = linesRun.result.lines;
      this.state.generatedLinesScriptId = scriptId;
    }
    for (const run of latest.values()) {
      if (!completed(run) || run.kind !== "rewrite_line" || !run.result?.line) continue;
      const sequence = Number(run.input?.sequence);
      if (sequence) this.state.lineRewriteSuggestions[this.lineRewriteKey(scriptId, sequence)] = run.result.line;
    }
    const latestPronunciations = latest.get("pronunciations");
    const pronunciationRun = completed(latestPronunciations) ? latestPronunciations : null;
    if (!pronunciationRun && this.state.pronunciationSuggestionsScriptId === scriptId) {
      this.state.pronunciationSuggestions = [];
      this.state.pronunciationSuggestionsScriptId = null;
    }
    if (pronunciationRun?.result?.lines?.length) {
      this.state.pronunciationSuggestions = pronunciationRun.result.lines;
      this.state.pronunciationSuggestionsScriptId = scriptId;
    }
    const latestPrompt = latest.get("prompt_suggestion");
    const promptRun = completed(latestPrompt) ? latestPrompt : null;
    if (!promptRun && this.state.selectedScriptId === scriptId) this.state.scriptPromptSuggestion = "";
    if (promptRun?.result?.suggestion) this.state.scriptPromptSuggestion = promptRun.result.suggestion;
  }

  applyCompletedProjectTextRuns() {
    const latest = this.state.textRuns.find((run) => !run.script_id && run.kind === "prompt_suggestion");
    if (!latest || latest.status !== "completed") this.state.projectPromptSuggestion = "";
    if (latest?.status === "completed" && latest.result?.suggestion) this.state.projectPromptSuggestion = latest.result.suggestion;
  }

  textRunLabel(kind) {
    return ({ prompt_suggestion: "完善上下文", lines: "生成台词", rewrite_line: "单行修改", pronunciations: "生成发音", script_import: "智能导入分析" }[kind] || "文本生成");
  }

  textRunHistory(scriptId = null) {
    return this.state.textRuns.filter((run) => run.project_id === this.state.project?.id && (scriptId ? run.script_id === scriptId : true));
  }

  renderTextRunHistory(scriptId = null) {
    const runs = this.textRunHistory(scriptId).slice(0, 30);
    const id = scriptId ? "scriptTextRunHistory" : "projectTextRunHistory";
    if (!runs.length) return `<section id="${id}" data-text-run-history="${scriptId ? "script" : "project"}" class="text-run-history"><p class="empty-list">还没有文本生成记录</p></section>`;
    return `<section id="${id}" data-text-run-history="${scriptId ? "script" : "project"}" class="text-run-history"><header><div><strong>生成记录</strong><small>输入、上下文和结果都会保留，可继续迭代</small></div></header><div class="text-run-list">${runs.map((run) => { const completed = run.status === "completed"; const resultLabel = run.kind === "script_import" ? "恢复待确认预览" : "采用结果"; const result = completed ? `<button class="button button-quiet button-small" type="button" data-action="adopt-text-run" data-run-id="${escapeHtml(run.id)}">${resultLabel}</button>` : ""; const continueButton = completed && run.kind !== "script_import" ? `<button class="button button-quiet button-small" type="button" data-action="continue-text-run" data-run-id="${escapeHtml(run.id)}">基于此继续</button>` : ""; return `<article class="text-run-row ${run.status}" data-run-id="${escapeHtml(run.id)}"><div class="text-run-row-top"><strong>${escapeHtml(this.textRunLabel(run.kind))}</strong><span class="text-run-stage">${escapeHtml(run.stage || run.status)}</span><time>${escapeHtml(formatDate(run.created_at))}</time></div><p>${escapeHtml(this.textRunPreview(run))}</p><details class="text-run-details"><summary>查看输入与上下文</summary><pre>${escapeHtml(this.textRunDetails(run))}</pre></details>${run.status === "failed" ? `<small class="text-run-error">${escapeHtml(run.error)}</small>` : ""}${result || continueButton ? `<div class="text-run-actions">${result}${continueButton}</div>` : ""}</article>`; }).join("")}</div></section>`;
  }

  textRunPreview(run) {
    if (run.status === "queued" || run.status === "running") return run.output_text || "正在等待模型返回…";
    if (run.kind === "lines" && run.result?.lines) return run.result.lines.map((line) => line.text).join(" · ");
    if (run.kind === "rewrite_line" && run.result?.line) return run.result.line.text || "";
    if (run.kind === "script_import" && run.result?.batch) return run.output_text || `${run.result.batch.drafts?.length || 0} 个台本候选`;
    return run.output_text || "已完成";
  }

  textRunDetails(run) {
    const input = run.input && typeof run.input === "object" ? JSON.stringify(run.input, null, 2) : "";
    const context = run.context && typeof run.context === "object" ? { ...run.context, items: undefined } : {};
    delete context.items;
    return `输入：\n${input || "{}"}\n\n上下文：\n${JSON.stringify(context, null, 2)}`;
  }

  textRunOutput(run) {
    if (run?.result && typeof run.result === "object") {
      if (typeof run.result.suggestion === "string") return run.result.suggestion;
      if (Array.isArray(run.result.lines)) return run.result.lines.map((line) => line?.text || line?.pronunciation || "").filter(Boolean).join("\n");
      if (run.result.line && typeof run.result.line === "object") return String(run.result.line.text || "");
    }
    return String(run?.output_text || "");
  }

  async ensureTextRunScript(run) {
    const scriptId = String(run?.script_id || "");
    if (!scriptId || !this.state.scripts.some((script) => script.id === scriptId)) return false;
    if (scriptId !== this.state.selectedScriptId) {
      this.closeSettingsDrawer({ restoreFocus: false });
      this.state.selectedScriptId = scriptId;
      this.state.scriptDetail = null;
      this.state.scriptPromptSuggestion = "";
      this.state.scriptPromptDraft = null;
      this.state.generatedLines = [];
      this.state.generatedLinesScriptId = null;
      this.state.pronunciationSuggestions = [];
      this.state.pronunciationSuggestionsScriptId = null;
      this.state.contextRevisions.script = [];
      this.state.textRunParentId = null;
    }
    this.showView("script");
    this.renderScript();
    await this.loadScriptDetail(scriptId);
    if (this.state.scriptDetail?.id !== scriptId) return false;
    this.openSettingsDrawer("script");
    return true;
  }

  captureTextGenerationFocus() {
    const active = document.activeElement;
    if (!active) return null;
    const projectForm = active.closest?.("#projectSettingsForm");
    if (projectForm) return { kind: "project", name: active.name, start: active.selectionStart, end: active.selectionEnd };
    const textForm = active.closest?.("#textGenerationForm");
    if (textForm) return { kind: "text", name: active.name, start: active.selectionStart, end: active.selectionEnd };
    const row = active.closest?.("[data-script-item]");
    if (row) return { kind: "line", sequence: row.dataset.lineNumber, name: active.name, start: active.selectionStart, end: active.selectionEnd };
    return null;
  }

  restoreTextGenerationFocus(focus) {
    if (!focus) return;
    let target = null;
    if (focus.kind === "project") target = byId("projectSettingsForm")?.elements?.namedItem(focus.name || "");
    if (focus.kind === "text") target = byId("textGenerationForm")?.elements?.namedItem(focus.name || "");
    if (focus.kind === "line") {
      const row = [...document.querySelectorAll("[data-script-item]")].find((item) => item.dataset.lineNumber === String(focus.sequence));
      target = row?.querySelector(`[name="${focus.name || ""}"]`);
    }
    if (!target) return;
    target.focus({ preventScroll: true });
    if (Number.isInteger(focus.start) && typeof target.setSelectionRange === "function") target.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
  }

  async continueTextRun(button) {
    const run = this.textRunFor(button?.dataset.runId);
    if (!run || run.status !== "completed") return;
    if (run.kind === "script_import") return;
    if (run.project_id !== this.state.project?.id) return;
    if (run.script_id && !await this.ensureTextRunScript(run)) return;
    this.state.textRunParentId = run.id;
    const output = this.textRunOutput(run);
    const input = run.input && typeof run.input === "object" ? run.input : {};
    if (!run.script_id) {
      this.showView("project");
      this.state.projectPromptDraft = output;
      this.renderProject();
      byId("projectSettingsForm")?.prompt?.focus();
      this.toast("已将上一轮结果放入项目上下文，可修改后继续");
      return;
    }
    if (run.kind === "prompt_suggestion") {
      this.state.scriptPromptDraft = output;
    } else if (run.kind === "lines") {
      this.state.textGenerationDrafts[run.script_id] = {
        ...this.textGenerationDraft(run.script_id),
        instruction: String(input.instruction || ""),
        line_count: String(input.line_count || "5"),
      };
    } else if (run.kind === "rewrite_line") {
      const sequence = Number(input.sequence);
      if (sequence) {
        this.state.lineRewriteInstructions[this.lineRewriteKey(run.script_id, sequence)] = String(input.instruction || "");
        this.state.scriptItemDrafts[run.script_id] ||= {};
        const current = this.state.scriptDetail?.items?.find((item) => Number(item.order) === sequence) || {};
        this.state.scriptItemDrafts[run.script_id][sequence] = {
          text: String(input.text ?? current.text ?? ""),
          pronunciation: String(input.pronunciation ?? current.pronunciation ?? ""),
          rewrite_instruction: String(input.instruction || ""),
        };
      }
    }
    this.renderScript();
    this.renderOpenSettingsDrawer("script");
    if (run.kind === "prompt_suggestion") byId("textGenerationForm")?.prompt?.focus();
    else if (run.kind === "lines") byId("textGenerationForm")?.instruction?.focus();
    else if (run.kind === "rewrite_line") document.querySelector(`[data-script-item][data-line-number="${Number(input.sequence)}"] [name="rewrite_instruction"]`)?.focus();
    this.toast("已载入上一轮输入，可修改后继续生成");
  }

  clearTextRunParent() {
    const drawer = this.state.settingsDrawer;
    this.state.textRunParentId = null;
    if (drawer === "script") this.renderOpenSettingsDrawer("script");
    else this.renderProject();
  }

  async adoptTextRun(button) {
    const run = this.textRunFor(button?.dataset.runId);
    if (!run || run.status !== "completed" || run.project_id !== this.state.project?.id) return;
    const output = this.textRunOutput(run);
    if (run.kind === "script_import") {
      const batch = run.result?.batch;
      if (!batch) return;
      const restored = await this.api.post(`/api/text-generation-runs/${enc(run.id)}/restore-script-import`, {});
      this.state.scriptImportBatch = restored.batch || batch;
      this.state.scriptImportError = "";
      this.showView("project");
      this.renderProject();
      byId("smartScriptImportSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
      this.toast("智能导入结果已恢复，请确认每个候选台本");
      return;
    }
    if (!run.script_id) {
      if (!output) return;
      this.showView("project");
      this.state.projectPromptDraft = output;
      this.renderProject();
      this.toast("结果已放入项目上下文草稿，请保存");
      return;
    }
    if (!await this.ensureTextRunScript(run)) return;
    if (run.kind === "prompt_suggestion") {
      this.state.scriptPromptSuggestion = output;
      this.renderOpenSettingsDrawer("script");
      this.toast("结果已放入角色特性建议，请确认并保存");
      return;
    }
    if (run.kind === "lines") {
      const lines = Array.isArray(run.result?.lines) ? run.result.lines : [];
      if (!lines.length) return;
      this.state.generatedLines = lines;
      this.state.generatedLinesScriptId = run.script_id;
      this.renderScript();
      this.renderOpenSettingsDrawer("script");
      this.toast("台词结果已放入候选区，请确认后加入编辑器");
      return;
    }
    if (run.kind === "rewrite_line") {
      const sequence = Number(run.input?.sequence);
      if (!sequence || !run.result?.line) return;
      this.state.lineRewriteSuggestions[this.lineRewriteKey(run.script_id, sequence)] = run.result.line;
      this.renderScript();
      this.renderOpenSettingsDrawer("script");
      this.toast(`第 ${sequence} 行结果已放入修改候选，请确认`);
      return;
    }
    if (run.kind === "pronunciations") {
      const lines = Array.isArray(run.result?.lines) ? run.result.lines : [];
      if (!lines.length) return;
      this.state.pronunciationSuggestions = lines;
      this.state.pronunciationSuggestionsScriptId = run.script_id;
      this.renderScript();
      this.toast("发音结果已放入批量候选区，请确认后保存");
    }
  }

  watchTextRun(runId) {
    if (this.textRunPolling.has(runId)) return;
    this.textRunPolling.add(runId);
    this.textRunPollFailures.delete(runId);
    const poll = async () => {
      try {
        const { run } = await this.api.get(`/api/text-generation-runs/${enc(runId)}`);
        const currentProject = this.state.project?.id;
        if (run.project_id === currentProject) this.upsertTextRun(run);
        if (run.status === "queued" || run.status === "running") {
          if (run.project_id === currentProject) this.refreshTextRunPanels();
          window.setTimeout(poll, 900);
          return;
        }
        this.textRunPolling.delete(runId);
        this.textRunPollFailures.delete(runId);
        if (run.project_id === currentProject) {
          this.invalidateTextRunDraft(run);
          const focus = this.captureTextGenerationFocus();
          if (run.kind === "script_import" && run.status === "completed" && run.result?.batch) {
            this.state.scriptImportBatch = run.result.batch;
            this.state.scriptImportError = "";
            this.renderProject();
            byId("smartScriptImportSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
          } else if (run.kind === "script_import" && run.status === "failed") {
            this.state.scriptImportError = run.error || "智能导入分析失败";
            this.renderProject();
          } else if (run.script_id === this.state.selectedScriptId) {
            this.captureTextGenerationDraft();
            this.applyCompletedTextRuns(run.script_id);
            this.renderScript();
            this.renderOpenSettingsDrawer("script");
            this.refreshTextRunPanels();
          } else if (!run.script_id) {
            this.applyCompletedProjectTextRuns();
            this.renderProject();
          } else {
            this.refreshTextRunPanels();
          }
          this.restoreTextGenerationFocus(focus);
          if (run.status === "completed") this.toast(`${this.textRunLabel(run.kind)}已完成`);
          else this.toast(run.error || `${this.textRunLabel(run.kind)}失败`, true);
        }
      } catch (error) {
        const failures = (this.textRunPollFailures.get(runId) || 0) + 1;
        if (failures <= 5 && this.textRunPolling.has(runId) && error?.status !== 404) {
          this.textRunPollFailures.set(runId, failures);
          window.setTimeout(poll, Math.min(5000, 700 * (2 ** (failures - 1))));
          return;
        }
        this.textRunPolling.delete(runId);
        this.textRunPollFailures.delete(runId);
      }
    };
    void poll();
  }

  refreshTextRunPanels() {
    this.refreshTextRunPanel(
      '[data-text-run-history="project"]',
      this.renderTextRunHistory(),
    );
    this.refreshTextRunPanel(
      '[data-text-run-history="script"]',
      this.renderTextRunHistory(this.state.selectedScriptId),
    );
    const projectSuggestion = document.querySelector('[data-prompt-suggestion="project"]');
    if (projectSuggestion) projectSuggestion.outerHTML = this.promptSuggestion(this.state.projectPromptSuggestion, "project");
    const scriptSuggestion = document.querySelector('[data-prompt-suggestion="script"]');
    if (scriptSuggestion) scriptSuggestion.outerHTML = this.promptSuggestion(this.state.scriptPromptSuggestion, "script");
  }

  refreshTextRunPanel(selector, html) {
    const current = document.querySelector(selector);
    if (!current) return;
    const list = current.querySelector(".text-run-list");
    const scrollTop = list?.scrollTop || 0;
    const stickToBottom = Boolean(
      list && list.scrollHeight - list.scrollTop - list.clientHeight < 24,
    );
    const openDetails = [...current.querySelectorAll("article[data-run-id] details[open]")]
      .map((details) => details.closest("article")?.dataset.runId)
      .filter(Boolean);
    current.outerHTML = html;
    const next = document.querySelector(selector);
    const nextList = next?.querySelector(".text-run-list");
    if (nextList) {
      nextList.scrollTop = stickToBottom
        ? nextList.scrollHeight
        : Math.min(scrollTop, nextList.scrollHeight);
    }
    for (const runId of openDetails) {
      const article = [...(next?.querySelectorAll("article[data-run-id]") || [])]
        .find((item) => item.dataset.runId === runId);
      const details = article?.querySelector("details");
      if (details) details.open = true;
    }
  }

  async restoreContextRevision(button) {
    const revisionId = button.dataset.revisionId;
    if (!revisionId) return;
    const { project, script } = await this.api.post(`/api/context-revisions/${enc(revisionId)}/restore`, {});
    if (project) {
      this.state.project = project;
      this.state.projectPromptDraft = null;
      this.state.projectPromptSuggestion = "";
      this.merge(this.state.projects, project);
    }
    if (script) {
      this.state.scriptDetail = { ...this.state.scriptDetail, ...script };
      this.state.scriptPromptDraft = null;
      this.state.scriptPromptSuggestion = "";
      this.merge(this.state.scripts, script);
    }
    await this.selectProject(this.state.project?.id || "", true);
    this.toast("已恢复上下文版本");
  }

  contextRevisionHistory(scope, scriptId = null) {
    return (scope === "script" ? this.state.contextRevisions.script : this.state.contextRevisions.project).filter((revision) => !scriptId || revision.script_id === scriptId);
  }

  async refreshContextRevisions(scope, id) {
    if (!id) return;
    const path = scope === "script"
      ? `/api/scripts/${enc(id)}/context-revisions?limit=100`
      : `/api/projects/${enc(id)}/context-revisions?limit=100`;
    const { revisions } = await this.api.get(path);
    if (scope === "script" && id === this.state.selectedScriptId) this.state.contextRevisions.script = revisions || [];
    if (scope === "project" && id === this.state.project?.id) this.state.contextRevisions.project = revisions || [];
  }

  renderContextRevisionHistory(scope, scriptId = null) {
    const revisions = this.contextRevisionHistory(scope, scriptId).slice(0, 20);
    const id = scriptId ? "scriptContextRevisionHistory" : "projectContextRevisionHistory";
    if (!revisions.length) return `<div id="${id}" class="context-revision-history"><p class="empty-list">保存后会在这里保留上下文版本</p></div>`;
    return `<details id="${id}" class="context-revision-history"><summary>上下文历史（${revisions.length} 个版本）</summary><div>${revisions.map((revision) => `<article class="context-revision-row"><div><strong>v${escapeHtml(revision.version)}</strong><time>${escapeHtml(formatDate(revision.created_at))}</time></div><p>${escapeHtml(revision.content || "（空）")}</p><button class="button button-quiet button-small" type="button" data-action="restore-context-revision" data-revision-id="${escapeHtml(revision.id)}">恢复此版本</button></article>`).join("")}</div></details>`;
  }

  renderProject() {
    const project = this.state.project;
    const canManage = project.can_manage;
    const projectSettings = canManage
      ? `<form id="projectSettingsForm" class="project-settings-form"><label class="field"><span>项目名称</span><input name="name" value="${escapeHtml(project.name)}" maxlength="80" required></label><label class="field"><span>项目说明</span><textarea name="description" maxlength="500">${escapeHtml(project.description)}</textarea></label><label class="field"><span>项目级上下文</span><textarea name="prompt" maxlength="12000" placeholder="统一定义世界观、角色关系、语言风格、输出格式和禁用项。">${escapeHtml(this.state.projectPromptDraft ?? project.prompt ?? "")}</textarea><small>所有角色都会继承这段上下文；每个角色可再保存自己的台词特性。</small></label><div class="form-actions"><button class="button button-quiet" type="button" data-action="suggest-project-prompt">让 AI 完善上下文</button><button class="button button-primary" type="submit">保存资料</button></div>${this.textRunParentNotice("project")}${this.promptSuggestion(this.state.projectPromptSuggestion, "project")}</form>`
      : `<div class="section-body"><p>${escapeHtml(project.description || "暂无项目说明")}</p><div class="readonly-prompt"><strong>项目级上下文</strong><p>${escapeHtml(project.prompt || "尚未设置")}</p></div></div>`;
    const memberActions = canManage
      ? '<form id="memberAddForm" class="member-add"><span>按用户名添加已由系统管理员创建的账户。</span><div class="inline-form"><input name="username" maxlength="64" required placeholder="member@example.com"><button class="button button-quiet" type="submit">添加成员</button></div></form>'
      : "";
    byId("projectContent").innerHTML = `
      <div class="view-header"><div><span class="eyebrow">项目管理</span><h1>项目资料与成员</h1><p>管理项目范围、共享上下文和参与成员。</p></div></div>
       <div class="project-layout">
         <section class="project-section work-section"><header class="section-heading"><div><h2>项目资料</h2><p>更新名称、说明和所有角色共用的项目级上下文</p></div></header>${projectSettings}${canManage ? this.renderContextRevisionHistory("project") : ""}</section>
        <section class="project-section work-section"><header class="section-heading"><div><h2>成员</h2><p>${project.member_count} 位项目成员</p></div></header><div id="memberList" class="member-list"><p class="empty-list">正在读取成员...</p></div>${memberActions}<p class="project-id-note">PROJECT ID · ${escapeHtml(project.id)}</p></section>
         ${this.smartScriptImportSection()}
         <section class="project-section work-section"><header class="section-heading"><div><h2>文本生成记录</h2><p>查看项目级上下文建议和其他文本任务的过程与结果</p></div></header>${this.renderTextRunHistory()}</section>
      </div>`;
    void this.loadMembers(project.id);
  }

  smartScriptImportSection() {
    const batch = this.state.scriptImportBatch;
    const error = this.state.scriptImportError;
    const modelReady = this.state.config.text_model?.configured === true;
    if (!batch) {
      if (error) {
        return `<section id="smartScriptImportSection" class="project-section work-section smart-import-section"><header class="section-heading"><div><h2>待确认数据异常</h2><p>${escapeHtml(error)}</p></div></header><div class="smart-import-empty"><div><strong>清理后可重新分析导入</strong><p>该操作只会删除无法读取的临时预览，不会删除已导入的正式台本。</p></div><button class="button button-danger" type="button" data-action="discard-corrupt-script-import">清理待确认数据</button></div></section>`;
      }
      return `<section id="smartScriptImportSection" class="project-section work-section smart-import-section"><header class="section-heading"><div><h2>AI 智能导入</h2><p>识别多台本、多角色和连续长文本</p></div></header><div class="smart-import-empty"><div><strong>从原始台本生成待确认预览</strong><p>已有行或段落会原样保留；连续文本只做分段和归类，不改写台词。</p>${modelReady ? "" : '<small>需要系统管理员先启用台词文本模型。</small>'}</div><button class="button button-primary" type="button" data-action="open-smart-script-import"${modelReady ? "" : " disabled"}><span aria-hidden="true">↑</span> AI 智能导入</button></div></section>`;
    }
    const drafts = Array.isArray(batch.drafts) ? batch.drafts : [];
    const excluded = Array.isArray(batch.excluded) ? batch.excluded : [];
    return `<section id="smartScriptImportSection" class="project-section work-section smart-import-section"><header class="section-heading"><div><h2>待确认的智能导入</h2><p>${drafts.length} 个台本候选 · ${Number(batch.dialogue_line_count || 0)} 条台词</p></div></header><form id="scriptImportConfirmForm" class="smart-import-confirm-form"><div class="smart-import-notice"><strong>尚未创建正式台本</strong><span>逐项核对名称、角色和每句原文后再确认。</span></div><div class="smart-import-draft-list">${drafts.map((draft, index) => this.smartScriptImportDraft(draft, index)).join("")}</div>${excluded.length ? `<details class="smart-import-excluded"><summary>查看未导入内容（${excluded.length} 段）</summary><ol>${excluded.map((item) => `<li><small>${escapeHtml(item.source_file || "")}</small><span>${escapeHtml(item.text || "")}</span></li>`).join("")}</ol></details>` : ""}<footer class="smart-import-actions"><button class="button button-quiet" type="button" data-action="discard-script-import">放弃本次分析</button><button class="button button-primary" type="submit">确认导入所选台本</button></footer></form></section>`;
  }

  smartScriptImportDraft(draft, index) {
    const lines = Array.isArray(draft.lines) ? draft.lines : [];
    const selected = draft.selected !== false;
    const script = draft.script || "未命名台本";
    const speaker = draft.speaker || "未识别角色";
    return `<article class="smart-import-draft" data-import-draft="${escapeHtml(draft.id)}"><label class="smart-import-select"><input type="checkbox" data-import-draft-select value="${escapeHtml(draft.id)}" aria-label="导入台本 ${escapeHtml(draft.name || String(index + 1))}"${selected ? " checked" : ""}><span>${String(index + 1).padStart(2, "0")}</span></label><div class="smart-import-draft-body"><div class="smart-import-draft-heading"><label class="field"><span>正式台本名称</span><input data-import-draft-name value="${escapeHtml(draft.name || "")}" maxlength="80"></label><p><span>台本：${escapeHtml(script)}</span><span>角色：${escapeHtml(speaker)}</span><span>${lines.length} 条</span></p></div><ol class="smart-import-lines">${lines.map((line, lineIndex) => `<li><span>${String(lineIndex + 1).padStart(2, "0")}</span><strong>${escapeHtml(line.text || "")}</strong></li>`).join("")}</ol></div></article>`;
  }

  async loadMembers(id) {
    if (this.loadingMembers === id) return;
    this.loadingMembers = id;
    const version = this.requestVersion;
    const token = ++this.loadingMembersToken;
    try {
      const { members } = await this.api.get(`/api/projects/${enc(id)}/members`);
      if (this.isCurrentProjectRequest(id, version) && token === this.loadingMembersToken) this.renderMembers(members);
    } catch (error) {
      if (this.isCurrentProjectRequest(id, version)) this.toast(error.message, true);
    } finally {
      if (token === this.loadingMembersToken) this.loadingMembers = null;
    }
  }

  renderMembers(members) {
    const canManage = this.state.project?.can_manage;
    const target = byId("memberList");
    if (!target) return;
    target.innerHTML = members.map((member) => `<div class="member-row"><span class="member-avatar">${escapeHtml(first(member.display_name || member.username))}</span><span class="member-copy"><strong>${escapeHtml(member.display_name)}</strong><small>@${escapeHtml(member.username)}</small></span>${canManage && member.role !== "owner" && member.id !== this.state.user.id ? `<select data-action="member-role" data-member-id="${escapeHtml(member.id)}"><option value="member"${member.role === "member" ? " selected" : ""}>成员</option><option value="admin"${member.role === "admin" ? " selected" : ""}>项目管理员</option></select><button class="icon-button" type="button" title="移除成员" data-action="remove-member" data-member-id="${escapeHtml(member.id)}">×</button>` : `<span class="member-role-label">${roleLabel(member.role)}</span><span></span>`}</div>`).join("");
  }

  async refreshJobs() {
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    const hasActiveJobs = this.state.jobs.some((job) => ["queued", "running"].includes(job.status));
    if (!projectId || !hasActiveJobs || this.loadingJobs || document.activeElement?.closest?.("form")) return;
    this.loadingJobs = true;
    try {
      const { jobs } = await this.api.get(`/api/jobs?limit=300&project_id=${enc(projectId)}`);
      if (!this.isCurrentProjectRequest(projectId, version)) return;
      this.state.jobs = jobs;
      this.renderScript();
      const scriptJobs = jobs.filter((job) => job.script_id === this.state.selectedScriptId);
      for (const job of scriptJobs) void this.loadJobDetail(job.id);
      const activeJobs = jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
      byId("queueSummary").textContent = activeJobs ? `${activeJobs} 个处理中` : "空闲";
      byId("workerDot").className = jobs.some((job) => job.status === "running") ? "online" : "";
    } catch (_) {
      // The manual refresh action reports request errors without repeating toasts.
    } finally {
      this.loadingJobs = false;
    }
  }

  pauseOtherAudio(current) {
    if (!(current instanceof HTMLAudioElement)) return;
    document.querySelectorAll("audio").forEach((audio) => {
      if (audio !== current) audio.pause();
    });
  }

  keydown(event) {
    if (this.generation.handleKeydown(event)) return;
    if (event.key === "Escape" && this.state.assetRename) {
      event.preventDefault();
      this.cancelAssetRename();
      return;
    }
    if (event.key === "Escape" && this.state.settingsDrawer) {
      event.preventDefault();
      this.closeSettingsDrawer();
      return;
    }
    if (this.state.settingsDrawer && event.key === "Tab") {
      const focusable = this.drawerFocusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
  }

  drawerFocusableElements() {
    const drawer = byId("assetSettingsDrawer");
    if (!drawer) return [];
    return [...drawer.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), audio[controls], [tabindex]:not([tabindex="-1"])')]
      .filter((element) => element.offsetParent !== null || element === document.activeElement);
  }

  startAssetRename(kind, id) {
    const assets = kind === "script" ? this.state.scripts : this.state.voices;
    if (!assets.some((asset) => asset.id === id)) return;
    this.state.assetRename = { kind, id };
    if (kind === "script") this.renderScript({ listOnly: true });
    else this.renderVoice({ listOnly: true });
    window.requestAnimationFrame(() => {
      const form = [...document.querySelectorAll('[data-asset-rename]')].find((item) => item.dataset.assetRename === kind && item.dataset.id === id);
      const input = form?.querySelector('input[name="name"]');
      input?.focus({ preventScroll: true });
      input?.select();
    });
  }

  cancelAssetRename() {
    const rename = this.state.assetRename;
    if (!rename) return;
    this.state.assetRename = null;
    if (rename.kind === "script") this.renderScript({ listOnly: true });
    else this.renderVoice({ listOnly: true });
    window.requestAnimationFrame(() => {
      const row = [...document.querySelectorAll('[data-action]')].find((item) => item.dataset.action === `select-${rename.kind}` && item.dataset.id === rename.id);
      row?.focus({ preventScroll: true });
    });
  }

  async saveAssetRename(form) {
    const kind = form.dataset.assetRename;
    const id = form.dataset.id;
    const projectId = this.state.project?.id;
    const requestVersion = this.requestVersion;
    const name = form.name.value.trim();
    if (!name) return;
    if (kind === "script") {
      const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, { name });
      if (!this.isCurrentScriptRequest(projectId, id, requestVersion)) return;
      this.merge(this.state.scripts, script);
      if (this.state.scriptDetail?.id === id) this.state.scriptDetail = { ...this.state.scriptDetail, ...script };
    } else {
      const voice = this.state.voices.find((item) => item.id === id);
      const notes = voice?.notes ?? this.state.voiceDetail?.notes ?? "";
      const { voice: updated } = await this.api.patch(`/api/voices/${enc(id)}`, { name, notes });
      if (!this.isCurrentVoiceRequest(projectId, id, requestVersion)) return;
      this.merge(this.state.voices, updated);
      if (this.state.voiceDetail?.id === id) this.state.voiceDetail = updated;
    }
    this.state.assetRename = null;
    this.render();
    this.showView(kind);
    window.requestAnimationFrame(() => {
      const row = [...document.querySelectorAll('[data-action]')].find((item) => item.dataset.action === `select-${kind}` && item.dataset.id === id);
      row?.focus({ preventScroll: true });
    });
    this.toast(kind === "script" ? "台本名称已保存" : "声音名称已保存");
  }

  openSettingsDrawer(kind) {
    const detail = kind === "script" ? this.state.scriptDetail : this.state.voiceDetail;
    const selectedId = kind === "script" ? this.state.selectedScriptId : this.state.selectedVoiceId;
    if (!detail || detail.id !== selectedId) return;
    this.drawerReturnFocus = document.activeElement;
    this.state.settingsDrawer = kind;
    this.renderSettingsDrawer();
    const backdrop = byId("assetSettingsBackdrop");
    const drawer = byId("assetSettingsDrawer");
    const appShell = byId("appShell");
    backdrop?.classList.add("open");
    drawer?.classList.add("open");
    backdrop?.setAttribute("aria-hidden", "false");
    drawer?.setAttribute("aria-hidden", "false");
    if (appShell) {
      appShell.inert = true;
      appShell.setAttribute("inert", "");
    }
    this.drawerFocusableElements()[0]?.focus();
  }

  closeSettingsDrawer(options = {}) {
    const drawer = byId("assetSettingsDrawer");
    if (!this.state.settingsDrawer && !drawer?.classList.contains("open")) return;
    const drawerKind = this.state.settingsDrawer;
    const returnFocus = this.drawerReturnFocus;
    this.state.settingsDrawer = null;
    const appShell = byId("appShell");
    byId("assetSettingsBackdrop")?.classList.remove("open");
    drawer?.classList.remove("open");
    byId("assetSettingsBackdrop")?.setAttribute("aria-hidden", "true");
    drawer?.setAttribute("aria-hidden", "true");
    if (appShell) {
      appShell.inert = false;
      appShell.removeAttribute("inert");
    }
    if (options.restoreFocus !== false) {
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
      else if (drawerKind) document.querySelector(`[data-action="open-settings-drawer"][data-kind="${drawerKind}"]`)?.focus({ preventScroll: true });
    }
    this.drawerReturnFocus = null;
  }

  renderSettingsDrawer() {
    const content = byId("assetSettingsContent");
    if (!content || !this.state.settingsDrawer) return;
    if (this.state.settingsDrawer === "script") {
      const detail = this.state.scriptDetail?.id === this.state.selectedScriptId ? this.state.scriptDetail : null;
      content.innerHTML = detail ? this.scriptSettingsDrawer(detail) : '<div class="empty-panel">正在读取台本设置...</div>';
      return;
    }
    const detail = this.state.voiceDetail?.id === this.state.selectedVoiceId ? this.state.voiceDetail : null;
    content.innerHTML = detail ? this.voiceSettingsDrawer(detail) : '<div class="empty-panel">正在读取声音设置...</div>';
  }

  renderOpenSettingsDrawer(kind) {
    if (this.state.settingsDrawer === kind) this.renderSettingsDrawer();
  }

  async click(event) {
    this.generation.handleDocumentClick(event);
    const closeButton = event.target.closest("[data-close-dialog]");
    if (closeButton) {
      byId(closeButton.dataset.closeDialog).close();
      return;
    }
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) {
      this.showView(viewButton.dataset.view);
      return;
    }
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    try {
      if (this.generation.handleAction(action, button)) return;
      if (action === "view") this.showView(button.dataset.viewTarget);
      if (action === "open-project-dialog") this.openDialog("projectDialog");
      if (action === "open-script") this.openDialog("scriptDialog");
      if (action === "open-smart-script-import") this.openDialog("smartScriptImportDialog");
      if (action === "open-voice") this.openDialog("voiceDialog");
      if (action === "open-settings-drawer") this.openSettingsDrawer(button.dataset.kind);
      if (action === "close-settings-drawer") this.closeSettingsDrawer();
      if (action === "refresh") await this.selectProject(this.state.project?.id || "", true);
       if (action === "select-script") { this.closeSettingsDrawer({ restoreFocus: false }); this.state.selectedScriptId = button.dataset.id; this.state.scriptDetail = null; this.state.scriptPromptSuggestion = ""; this.state.scriptPromptDraft = null; this.state.generatedLines = []; this.state.generatedLinesScriptId = null; this.state.pronunciationSuggestions = []; this.state.pronunciationSuggestionsScriptId = null; this.state.textRunParentId = null; this.storePosition(); this.renderScript(); for (const job of this.state.jobs.filter((item) => item.script_id === this.state.selectedScriptId)) void this.loadJobDetail(job.id); }
      if (action === "select-voice") { this.closeSettingsDrawer({ restoreFocus: false }); this.state.selectedVoiceId = button.dataset.id; this.state.voiceDetail = null; this.storePosition(); this.renderVoice(); this.renderScript(); }
      if (action === "rewrite-line") await this.runBusy(button, () => this.rewriteScriptLine(button));
      if (action === "generate-pronunciations") await this.runBusy(button, () => this.generateAllPronunciations());
      if (action === "adopt-all-pronunciations") this.adoptAllPronunciations();
      if (action === "discard-all-pronunciations") this.discardAllPronunciations();
      if (action === "adopt-line-rewrite") this.adoptLineRewrite(button);
      if (action === "discard-line-rewrite") this.discardLineRewrite(button);
      if (action === "select-generation") await this.selectGeneration(button.dataset);
      if (action === "clear-generation-selection") await this.clearGenerationSelection(Number(button.dataset.sequence));
      if (action === "export-script-audio") this.exportScriptAudio(button.dataset.scope);
      if (action === "delete-generation") await this.deleteGeneration(button.dataset.jobId, button.dataset.itemId);
      if (action === "delete-all-generations") await this.deleteAllGenerations();
      if (action === "delete-script") await this.deleteScript();
      if (action === "remove-member") await this.removeMember(button.dataset.memberId);
      if (action === "discard-script-import") await this.runBusy(button, () => this.discardSmartScriptImport());
      if (action === "discard-corrupt-script-import") await this.runBusy(button, () => this.discardCorruptSmartScriptImport());
       if (action === "suggest-project-prompt") await this.runBusy(button, () => this.suggestProjectPrompt());
       if (action === "suggest-script-prompt") await this.runBusy(button, () => this.suggestScriptPrompt());
       if (action === "save-character-context") await this.runBusy(button, () => this.saveCharacterContext());
       if (action === "adopt-project-prompt") this.adoptProjectPrompt();
       if (action === "adopt-script-prompt") this.adoptScriptPrompt();
       if (action === "adopt-generated-lines") this.adoptGeneratedLines();
       if (action === "adopt-text-run") await this.adoptTextRun(button);
       if (action === "continue-text-run") await this.continueTextRun(button);
       if (action === "clear-text-run-parent") this.clearTextRunParent();
       if (action === "restore-context-revision") await this.runBusy(button, () => this.restoreContextRevision(button));
    } catch (error) { this.toast(error.message, true); }
  }

  input(event) {
    const input = event.target;
    if (input.name === "prompt" && input.closest("#projectSettingsForm")) {
      this.state.projectPromptDraft = input.value;
    }
    if (input.name === "prompt" && input.closest("#textGenerationForm")) {
      this.state.scriptPromptDraft = input.value;
    }
    if ((input.name === "instruction" || input.name === "line_count") && input.closest("#textGenerationForm")) {
      this.captureTextGenerationDraft(input.closest("#textGenerationForm"));
    }
    if (["text", "pronunciation", "rewrite_instruction"].includes(input.name) && input.closest("[data-script-item]")) {
      this.captureScriptItemDraft(input.closest("[data-script-item]"));
    }
    if (input.name === "rewrite_instruction" && input.closest("[data-script-item]")) {
      const sequence = Number(input.closest("[data-script-item]").dataset.lineNumber);
      this.state.lineRewriteInstructions[this.lineRewriteKey(this.state.selectedScriptId, sequence)] = input.value;
    }
    if (input.id === "scriptSearch") {
      this.state.scriptSearchQuery = input.value;
      this.renderScript({ listOnly: true });
    }
    if (input.id === "voiceSearch") {
      this.state.voiceSearchQuery = input.value;
      this.renderVoice({ listOnly: true });
    }
    if (input.matches("[data-import-draft-name]")) {
      const draftId = input.closest("[data-import-draft]")?.dataset.importDraft;
      const draft = this.state.scriptImportBatch?.drafts?.find((item) => item.id === draftId);
      if (draft) draft.name = input.value;
    }
    this.generation.handleInput(input);
  }

  captureScriptItemDraft(row) {
    const scriptId = this.state.selectedScriptId;
    const sequence = Number(row?.dataset.lineNumber);
    if (!scriptId || !sequence) return;
    this.state.scriptItemDrafts[scriptId] ||= {};
    this.state.scriptItemDrafts[scriptId][sequence] = {
      text: row.querySelector('[name="text"]').value,
      pronunciation: row.querySelector('[name="pronunciation"]').value,
      rewrite_instruction: row.querySelector('[name="rewrite_instruction"]')?.value || "",
    };
  }

  async change(event) {
    const input = event.target;
    try {
      if (input.dataset.action === "file-enabled") await this.updateVoiceFile(input.dataset.fileId, { enabled: input.checked });
      if (input.dataset.action === "file-emotion") await this.updateVoiceFile(input.dataset.fileId, { emotion_tag: input.value });
      if (input.dataset.action === "member-role") await this.updateMemberRole(input.dataset.memberId, input.value);
      if (input.matches("[data-import-draft-select]")) {
        const draft = this.state.scriptImportBatch?.drafts?.find((item) => item.id === input.value);
        if (draft) draft.selected = input.checked;
      }
    } catch (error) {
      if (["file-enabled", "file-emotion"].includes(input.dataset.action)) this.renderVoiceSettingsFiles();
      this.toast(error.message, true);
    }
  }

  async submit(event) {
    const form = event.target;
    if (!form.matches("form")) return;
    event.preventDefault();
    const button = event.submitter || form.querySelector('button[type="submit"]');
    try {
      if (form.id === "projectCreateForm") await this.createProject(form);
      if (form.id === "scriptCreateForm") await this.createScript(form);
      if (form.id === "smartScriptImportForm") await this.runBusy(button, () => this.analyzeSmartScriptImport(form));
      if (form.id === "scriptImportConfirmForm") await this.runBusy(button, () => this.confirmSmartScriptImport(form));
      if (form.id === "voiceCreateForm") await this.createVoice(form);
      if (form.id === "assetRenameForm") await this.runBusy(button, () => this.saveAssetRename(form));
      if (form.id === "scriptSettingsForm") await this.runBusy(button, () => this.saveScriptSettings(form));
      if (form.id === "scriptItemsForm") await this.runBusy(button, () => this.saveScriptItems(form));
      if (form.id === "voiceSettingsForm") await this.saveVoiceSettings(form);
      if (form.id === "voiceAppendForm") await this.appendVoiceFiles(form);
      if (form.id === "projectSettingsForm") await this.runBusy(button, () => this.saveProject(form));
      if (form.id === "memberAddForm") await this.addMember(form);
      if (form.id === "generationOptionsForm") await this.runBusy(button, () => this.submitGenerationOptions());
      if (form.id === "textGenerationForm") await this.runBusy(button, () => this.generateText(form));
    } catch (error) {
      const message = String(error?.message || "请求失败，请稍后重试");
      if (form.id === "smartScriptImportForm") this.setSmartScriptImportMessage(message, true);
      this.toast(message, true);
    }
  }

  async createProject(form) { const { project } = await this.api.post("/api/projects", { name: form.name.value.trim(), description: form.description.value.trim() }); this.state.projects.push(project); form.reset(); byId("projectDialog").close(); await this.selectProject(project.id, true); this.showView("script"); this.toast("项目已创建"); }
  async createScript(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { script } = await this.api.postForm("/api/scripts", data); this.state.scripts.unshift(script); this.state.selectedScriptId = script.id; this.state.scriptDetail = null; form.reset(); byId("scriptUploadFileName").textContent = "选择台本文件"; byId("scriptDialog").close(); this.render(); this.showView("script"); this.toast("台本已导入"); }
  async analyzeSmartScriptImport(form) {
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    this.setSmartScriptImportMessage("正在上传并分析，长文本可能需要一些时间，请稍候。");
    if (!projectId) throw new Error("请先选择项目");
    const { run } = await this.api.postForm(`/api/projects/${enc(projectId)}/script-imports/analyze-run`, new FormData(form));
    if (!this.isCurrentProjectRequest(projectId, version)) return;
    this.upsertTextRun(run);
    this.state.scriptImportError = "";
    this.setSmartScriptImportMessage();
    form.reset();
    byId("smartScriptImportFileName").textContent = "选择一个或多个台本文件";
    byId("smartScriptImportDialog").close();
    this.renderProject();
    this.watchTextRun(run.id);
    document.querySelector('[data-text-run-history="project"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
    this.toast("已加入智能导入分析队列，可在记录中查看进度");
  }
  async confirmSmartScriptImport(form) {
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    const batchId = this.state.scriptImportBatch?.batch_id;
    if (!projectId || !batchId) throw new Error("没有可确认的智能导入结果");
    const drafts = [...form.querySelectorAll("[data-import-draft]")]
      .filter((row) => row.querySelector("[data-import-draft-select]")?.checked)
      .map((row) => ({
        id: row.dataset.importDraft,
        name: row.querySelector("[data-import-draft-name]").value.trim(),
      }));
    if (!drafts.length) throw new Error("请至少选择一个台本");
    if (drafts.some((draft) => !draft.name)) throw new Error("所选台本名称不能为空");
    const { scripts } = await this.api.post(`/api/projects/${enc(projectId)}/script-imports/confirm`, {
      batch_id: batchId,
      drafts,
    });
    if (!this.isCurrentProjectRequest(projectId, version) || this.state.scriptImportBatch?.batch_id !== batchId) return;
    const createdIds = new Set(scripts.map((script) => script.id));
    this.state.scripts = [...scripts, ...this.state.scripts.filter((script) => !createdIds.has(script.id))];
    this.state.scriptImportBatch = null;
    this.state.scriptImportError = "";
    this.state.selectedScriptId = scripts[0]?.id || this.state.selectedScriptId;
    this.state.scriptDetail = null;
    this.storePosition();
    this.render();
    this.showView("script");
    this.toast(`已导入 ${scripts.length} 个台本`);
  }
  async discardSmartScriptImport() {
    const batch = this.state.scriptImportBatch;
    if (!batch || !window.confirm("确定放弃这次 AI 分析结果吗？")) return;
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    const batchId = batch.batch_id;
    if (!projectId || !batchId) return;
    await this.api.delete(`/api/projects/${enc(projectId)}/script-imports/${enc(batchId)}`);
    if (!this.isCurrentProjectRequest(projectId, version) || this.state.scriptImportBatch?.batch_id !== batchId) return;
    this.state.scriptImportBatch = null;
    this.state.scriptImportError = "";
    this.renderProject();
    this.toast("已放弃本次分析");
  }
  async discardCorruptSmartScriptImport() {
    if (!window.confirm("确定清理无法读取的待确认数据吗？")) return;
    const projectId = this.state.project?.id;
    const version = this.requestVersion;
    if (!projectId) return;
    await this.api.delete(`/api/projects/${enc(projectId)}/script-imports/pending`);
    if (!this.isCurrentProjectRequest(projectId, version)) return;
    this.state.scriptImportBatch = null;
    this.state.scriptImportError = "";
    this.renderProject();
    this.toast("已清理待确认数据");
  }
  async createVoice(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { voice } = await this.api.postForm("/api/voices", data); this.state.voices.unshift(voice); this.state.selectedVoiceId = voice.id; this.state.voiceDetail = null; form.reset(); byId("voiceFileName").textContent = "选择参考录音"; byId("voiceDialog").close(); this.render(); this.showView("voice"); this.toast("声音已创建"); }
   async saveScriptSettings(form) { const projectId = this.state.project?.id; const id = this.state.selectedScriptId; const requestVersion = this.requestVersion; const version = this.state.scriptDetail?.version; const payload = { name: form.name.value.trim(), prompt: this.currentCharacterContext() }; if (Number.isInteger(version)) payload.version = version; const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, payload); if (!this.isCurrentScriptRequest(projectId, id, requestVersion)) return; this.merge(this.state.scripts, script); this.state.scriptDetail = { ...this.state.scriptDetail, ...script }; await this.refreshContextRevisions("script", id); this.render(); this.toast("名称已保存"); }
  async saveScriptItems(form, options = {}) { const projectId = this.state.project?.id; const id = this.state.selectedScriptId; const requestVersion = this.requestVersion; const version = this.state.scriptDetail?.version; const items = [...form.querySelectorAll("[data-script-item]")].map((row) => ({ text: row.querySelector('[name="text"]').value, pronunciation: row.querySelector('[name="pronunciation"]').value, rewrite_instruction: row.querySelector('[name="rewrite_instruction"]')?.value || "" })); const payload = { items }; if (Number.isInteger(version)) payload.version = version; const { script } = await this.api.put(`/api/scripts/${enc(id)}/items`, payload); if (!this.isCurrentScriptRequest(projectId, id, requestVersion)) return null; this.state.scriptDetail = script; this.merge(this.state.scripts, script); delete this.state.scriptItemDrafts[id]; Object.keys(this.state.lineRewriteSuggestions).filter((key) => key.startsWith(`${id}:`)).forEach((key) => delete this.state.lineRewriteSuggestions[key]); if (options.rerender !== false) this.renderScript(); if (options.notify !== false) this.toast("台词与发音已保存"); return script; }
  scriptItemsNeedSave(form) {
    const detail = this.state.scriptDetail;
    if (!detail || detail.id !== this.state.selectedScriptId) return false;
    const rows = [...form.querySelectorAll("[data-script-item]")];
    if (rows.length !== detail.items.length) return true;
    return rows.some((row, index) => {
      const item = detail.items[index] || {};
      return row.querySelector('[name="text"]')?.value !== String(item.text ?? "")
        || row.querySelector('[name="pronunciation"]')?.value !== String(item.pronunciation ?? "")
        || row.querySelector('[name="rewrite_instruction"]')?.value !== String(item.rewrite_instruction ?? "");
    });
  }
  async saveVoiceSettings(form) { const projectId = this.state.project?.id; const id = this.state.selectedVoiceId; const requestVersion = this.requestVersion; const { voice } = await this.api.patch(`/api/voices/${enc(id)}`, { name: form.name.value.trim(), notes: form.notes.value.trim() }); if (!this.isCurrentVoiceRequest(projectId, id, requestVersion)) return; this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.render(); this.toast("声音信息已保存"); }
  async appendVoiceFiles(form) { const projectId = this.state.project?.id; const id = this.state.selectedVoiceId; const requestVersion = this.requestVersion; const { voice } = await this.api.postForm(`/api/voices/${enc(id)}/files`, new FormData(form)); if (!this.isCurrentVoiceRequest(projectId, id, requestVersion)) return; this.state.voiceDetail = voice; this.merge(this.state.voices, voice); form.reset(); this.renderVoiceSettingsFiles(); this.renderVoice(); this.toast("参考录音已追加"); }
  async updateVoiceFile(fileId, changes) { const projectId = this.state.project?.id; const id = this.state.selectedVoiceId; const requestVersion = this.requestVersion; const { voice } = await this.api.patch(`/api/voices/${enc(id)}/files/${enc(fileId)}`, changes); if (!this.isCurrentVoiceRequest(projectId, id, requestVersion)) return; this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.renderVoiceSettingsFiles(); this.renderVoice(); this.toast("录音设置已更新"); }
  async submitGenerationOptions() {
    const submission = this.generation.submission();
    if (!submission) return;
    if (!await this.generateScript(submission.lineNumber, submission.configurations)) return;
    this.generation.finish();
  }

  async generateScript(lineNumber = null, configurations = []) {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const voices = this.state.voices.filter((item) => item.enabled_file_count);
    const models = this.state.config.models.filter((model) => model.available === true);
    const seen = new Set();
    const validConfigurations = configurations.flatMap((configuration) => {
      const voiceId = configuration?.voiceId;
      const modelId = configuration?.modelId;
      const key = `${voiceId}\u0000${modelId}`;
      if (!voices.some((voice) => voice.id === voiceId) || !models.some((model) => model.id === modelId) || seen.has(key)) return [];
      seen.add(key);
      return [{ voiceId, modelId, candidateCount: Math.min(4, Math.max(1, Number(configuration.candidateCount) || 1)) }];
    });
    if (!script || !validConfigurations.length || validConfigurations.length !== configurations.length || validConfigurations.length > 4) { this.toast("请检查生成配置（最多 4 条且组合不能重复）", true); return false; }
    const form = byId("scriptItemsForm");
    if (form && this.scriptItemsNeedSave(form) && !await this.saveScriptItems(form, { notify: false, rerender: false })) return false;
    const requests = [];
    const { baseSeed, seedStride } = generationSeedPlan(validConfigurations);
    const grouped = new Map();
    for (const configuration of validConfigurations) {
      const list = grouped.get(configuration.candidateCount) || [];
      list.push(configuration);
      grouped.set(configuration.candidateCount, list);
    }
    for (const group of grouped.values()) {
      const voiceIds = [...new Set(group.map((item) => item.voiceId))];
      const modelIds = [...new Set(group.map((item) => item.modelId))];
      if (voiceIds.length * modelIds.length === group.length) {
        requests.push({ voiceIds, modelIds, candidateCount: group[0].candidateCount });
      } else {
        group.forEach((configuration) => requests.push({ voiceIds: [configuration.voiceId], modelIds: [configuration.modelId], candidateCount: configuration.candidateCount }));
      }
    }
    const results = await Promise.allSettled(requests.map((request) => {
      const data = new FormData();
      data.set("project_id", projectId);
      data.set("script_id", script.id);
      data.set("voice_id", request.voiceIds[0]);
      data.set("voice_ids", JSON.stringify(request.voiceIds));
      data.set("model_id", request.modelIds[0]);
      data.set("model_ids", JSON.stringify(request.modelIds));
      data.set("candidate_count", String(request.candidateCount));
      data.set("base_seed", String(baseSeed));
      data.set("seed_stride", String(seedStride));
      data.set("reference_emotion", "all");
      data.set("generation_settings", "{}");
      data.set("name", lineNumber === null ? script.name : `${script.name} · 第 ${lineNumber} 行`);
      if (lineNumber !== null) data.set("line_number", String(lineNumber));
      return this.api.postForm("/api/jobs", data);
    }));
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return false;
    const jobs = results.flatMap((result) => result.status === "fulfilled" ? (result.value.jobs || [result.value.job]) : []);
    const failures = results.filter((result) => result.status === "rejected").length;
    if (!jobs.length) { this.toast("生成任务全部提交失败，请稍后重试", true); return false; }
    this.state.jobs.unshift(...jobs);
    for (const job of jobs) this.state.jobDetails[job.id] = null;
    this.renderScript();
    const activeJobs = this.state.jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    byId("queueSummary").textContent = `${activeJobs} 个处理中`;
    byId("workerDot").className = "online";
    const candidateCount = validConfigurations.reduce((total, configuration) => total + configuration.candidateCount, 0);
    const partial = failures ? `，${failures} 个请求失败` : "";
    this.toast(lineNumber === null ? `全部台词已加入生成队列（${jobs.length} 个任务，每行 ${candidateCount} 条候选${partial}）` : `第 ${lineNumber} 行已加入生成队列（${jobs.length} 个任务，共 ${candidateCount} 条候选${partial}）`, Boolean(failures));
    for (const job of jobs) void this.loadJobDetail(job.id, true);
    return true;
  }
  async selectGeneration(data) {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    const result = await this.api.post(`/api/scripts/${enc(scriptId)}/selections`, {
      sequence: Number(data.sequence),
      job_id: data.jobId,
      item_id: data.itemId,
      candidate_id: data.candidateId,
    });
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    const detail = this.state.scriptDetail;
    if (detail) {
      detail.selections = [...(detail.selections || []).filter((row) => Number(row.sequence) !== Number(data.sequence)), result.selection];
    }
    this.renderScript();
    this.toast(`第 ${data.sequence} 行已标记为采纳`);
  }
  async clearGenerationSelection(sequence) {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    await this.api.delete(`/api/scripts/${enc(scriptId)}/selections/${enc(sequence)}`);
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    const detail = this.state.scriptDetail;
    if (detail) detail.selections = (detail.selections || []).filter((row) => Number(row.sequence) !== sequence);
    this.renderScript();
    this.toast(`第 ${sequence} 行已取消采纳`);
  }
  exportScriptAudio(scope) {
    const detail = this.state.scriptDetail;
    if (scope === "accepted" && (!detail || (detail.selections || []).length < detail.items.length)) {
      this.toast("请先为每一行选择采纳音频", true);
      return;
    }
    window.location.href = `/api/scripts/${enc(this.state.selectedScriptId)}/audio-export?scope=${enc(scope)}`;
  }
  async deleteGeneration(jobId, itemId) {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    const job = this.state.jobs.find((item) => item.id === jobId);
    if (!job || !itemId) return;
    const detail = this.state.jobDetails[jobId];
    const item = detail?.items?.find((entry) => entry.id === itemId);
    if (["queued", "running"].includes(item?.status || job.status)) return;
    if (!window.confirm("确定删除这条生成结果吗？对应音频文件也会被删除。")) return;
    await this.api.delete(`/api/jobs/${enc(jobId)}/items/${enc(itemId)}`);
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    if (detail?.items) {
      detail.items = detail.items.filter((entry) => entry.id !== itemId);
      detail.selections = (detail.selections || []).filter((entry) => entry.job_id !== jobId || entry.item_id !== itemId);
      job.total_items = detail.items.length;
      job.completed_items = detail.items.filter((entry) => entry.status === "completed").length;
    }
    if (!detail?.items?.length || Number(job.total_items) === 0) {
      this.state.jobs = this.state.jobs.filter((entry) => entry.id !== jobId);
      delete this.state.jobDetails[jobId];
    }
    if (this.state.scriptDetail?.id === this.state.selectedScriptId) {
      this.state.scriptDetail.selections = (this.state.scriptDetail.selections || []).filter(
        (entry) => entry.job_id !== jobId || entry.item_id !== itemId,
      );
    }
    this.renderScript();
    this.toast("生成结果已删除");
  }
  async deleteAllGenerations() {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    const jobs = this.generationJobsForScript(scriptId).filter((job) => !["queued", "running"].includes(job.status));
    if (!jobs.length) return;
    if (!window.confirm(`确定删除当前台本的 ${jobs.length} 个生成结果吗？对应音频文件也会被删除。`)) return;
    for (const job of jobs) await this.api.delete(`/api/jobs/${enc(job.id)}`);
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    const deletedIds = new Set(jobs.map((job) => job.id));
    this.state.jobs = this.state.jobs.filter((job) => !deletedIds.has(job.id));
    if (this.state.scriptDetail) this.state.scriptDetail.selections = (this.state.scriptDetail.selections || []).filter((entry) => !deletedIds.has(entry.job_id));
    for (const job of jobs) delete this.state.jobDetails[job.id];
    this.renderScript();
    this.renderOpenSettingsDrawer("script");
    this.toast(`${jobs.length} 个生成结果已删除`);
  }
  async saveProject(form) { const projectId = this.state.project?.id; const requestVersion = this.requestVersion; const { project } = await this.api.patch(`/api/projects/${enc(projectId)}`, { name: form.name.value.trim(), description: form.description.value.trim(), prompt: form.prompt.value }); if (!this.isCurrentProjectRequest(projectId, requestVersion)) return; this.state.project = project; this.state.projectPromptSuggestion = ""; this.state.projectPromptDraft = null; this.merge(this.state.projects, project); await this.refreshContextRevisions("project", projectId); this.render(); this.toast("项目资料已保存"); }
    async suggestProjectPrompt() { const projectId = this.state.project?.id; const requestVersion = this.requestVersion; const form = byId("projectSettingsForm"); const draft = form?.prompt?.value || ""; const payload = { kind: "prompt_suggestion", goal: draft }; const parent = this.textRunParent("project"); if (parent) payload.parent_run_id = parent.id; const { run } = await this.api.post(`/api/projects/${enc(projectId)}/text-generation-runs`, payload); if (!this.isCurrentProjectRequest(projectId, requestVersion)) return; this.upsertTextRun(run); this.state.textRunParentId = null; this.state.projectPromptDraft = draft; this.renderProject(); this.watchTextRun(run.id); this.toast("已加入文本生成队列，可在记录中查看进度"); }
  adoptProjectPrompt() { const form = byId("projectSettingsForm"); if (!form || !this.state.projectPromptSuggestion) return; this.state.projectPromptDraft = this.state.projectPromptSuggestion; form.prompt.value = this.state.projectPromptDraft; this.toast("建议已放入编辑框，请保存"); }
  currentCharacterContext() { return byId("textGenerationForm")?.prompt?.value ?? this.state.scriptPromptDraft ?? this.state.scriptDetail?.prompt ?? ""; }
    async persistCharacterContext(options = {}) { const projectId = this.state.project?.id; const id = this.state.selectedScriptId; const requestVersion = this.requestVersion; const name = this.state.scriptDetail?.name || ""; const prompt = this.currentCharacterContext(); const savedPrompt = String(this.state.scriptDetail?.prompt || ""); if (prompt.trim() === savedPrompt.trim()) { this.state.scriptPromptDraft = null; if (options.clearSuggestion) this.state.scriptPromptSuggestion = ""; return this.state.scriptDetail; } const payload = { name, prompt }; const version = this.state.scriptDetail?.version; if (Number.isInteger(version)) payload.version = version; const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, payload); if (!this.isCurrentScriptRequest(projectId, id, requestVersion)) return null; this.merge(this.state.scripts, script); this.state.scriptDetail = { ...this.state.scriptDetail, ...script }; this.state.scriptPromptDraft = null; if (options.clearSuggestion) this.state.scriptPromptSuggestion = ""; await this.refreshContextRevisions("script", id); return script; }
  async saveCharacterContext() { const projectId = this.state.project?.id; const id = this.state.selectedScriptId; const requestVersion = this.requestVersion; if (!await this.persistCharacterContext({ clearSuggestion: true }) || !this.isCurrentScriptRequest(projectId, id, requestVersion)) return; this.renderOpenSettingsDrawer("script"); this.toast("角色台词特性已保存"); }
    async suggestScriptPrompt() { const projectId = this.state.project?.id; const id = this.state.selectedScriptId; const requestVersion = this.requestVersion; const draft = this.currentCharacterContext(); const payload = { kind: "prompt_suggestion", goal: draft }; const parent = this.textRunParent("script", id); if (parent) payload.parent_run_id = parent.id; const { run } = await this.api.post(`/api/scripts/${enc(id)}/text-generation-runs`, payload); if (!this.isCurrentScriptRequest(projectId, id, requestVersion)) return; this.upsertTextRun(run); this.state.textRunParentId = null; this.state.scriptPromptDraft = draft; this.renderOpenSettingsDrawer("script"); this.watchTextRun(run.id); this.toast("已加入文本生成队列，可在记录中查看进度"); }
  adoptScriptPrompt() { const form = byId("textGenerationForm"); if (!form || !this.state.scriptPromptSuggestion) return; this.state.scriptPromptDraft = this.state.scriptPromptSuggestion; form.prompt.value = this.state.scriptPromptDraft; this.toast("建议已放入角色特性框，请保存"); }
   async generateText(form) { const projectId = this.state.project?.id; const id = this.state.selectedScriptId; const requestVersion = this.requestVersion; const instruction = form.instruction?.value.trim() || ""; const lineCount = Number(form.line_count.value); this.captureTextGenerationDraft(form); if (!await this.persistCharacterContext() || !this.isCurrentScriptRequest(projectId, id, requestVersion)) return; const payload = { kind: "lines", instruction, line_count: lineCount }; const parent = this.textRunParent("script", id); if (parent) payload.parent_run_id = parent.id; const { run } = await this.api.post(`/api/scripts/${enc(id)}/text-generation-runs`, payload); if (!this.isCurrentScriptRequest(projectId, id, requestVersion)) return; this.upsertTextRun(run); this.state.textRunParentId = null; this.renderOpenSettingsDrawer("script"); this.watchTextRun(run.id); this.toast("已加入文本生成队列，可在记录中查看进度"); }
  async generateAllPronunciations() {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    const form = byId("scriptItemsForm");
    if (form && this.scriptItemsNeedSave(form) && !await this.saveScriptItems(form, { notify: false, rerender: false })) return;
    const currentPrompt = this.currentCharacterContext().trim();
    const savedPrompt = String(this.state.scriptDetail?.prompt || "").trim();
    if (currentPrompt !== savedPrompt && !await this.persistCharacterContext()) return;
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    const version = this.state.scriptDetail?.version;
    const payload = { kind: "pronunciations" };
    if (Number.isInteger(version)) payload.version = version;
    const parent = this.textRunParent("script", scriptId);
    if (parent) payload.parent_run_id = parent.id;
    const { run } = await this.api.post(`/api/scripts/${enc(scriptId)}/text-generation-runs`, payload);
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    this.upsertTextRun(run);
    this.state.textRunParentId = null;
    this.renderScript();
    this.watchTextRun(run.id);
    this.toast("已加入发音生成队列，可在记录中查看进度");
  }
  adoptAllPronunciations() {
    const detail = this.state.scriptDetail;
    const lines = this.state.pronunciationSuggestionsScriptId === detail?.id ? this.state.pronunciationSuggestions : [];
    if (!detail || !lines.length) return;
    document.querySelectorAll("#scriptItemsForm [data-script-item]").forEach((row) => this.captureScriptItemDraft(row));
    const drafts = this.state.scriptItemDrafts[detail.id] ||= {};
    const pronunciations = new Map(lines.map((line) => [Number(line.sequence), line.pronunciation]));
    detail.items.forEach((item, index) => {
      const sequence = Number(item.order || index + 1);
      const pronunciation = pronunciations.get(sequence);
      if (pronunciation === undefined) return;
      const draft = drafts[sequence] || {
        text: item.text,
        pronunciation: item.pronunciation,
        rewrite_instruction: item.rewrite_instruction || "",
      };
      drafts[sequence] = { ...draft, pronunciation };
    });
    this.state.pronunciationSuggestions = [];
    this.state.pronunciationSuggestionsScriptId = null;
    this.renderScript();
    this.toast("已采用全部发音候选，请保存台词");
  }
  discardAllPronunciations() {
    this.state.pronunciationSuggestions = [];
    this.state.pronunciationSuggestionsScriptId = null;
    this.renderScript();
  }
  adoptGeneratedLines() { const detail = this.state.scriptDetail; const lines = this.state.generatedLinesScriptId === detail?.id ? this.state.generatedLines : []; if (!detail || !lines.length) return; const items = [...detail.items]; for (const line of lines) items.push({ order: items.length + 1, source_line: items.length + 1, text: line.text, pronunciation: line.pronunciation, generated_text: line.text, direction: "flat", emphasis: [], hold_units: [], raw_mode: false }); this.state.scriptDetail = { ...detail, items, item_count: items.length }; this.state.generatedLines = []; this.state.generatedLinesScriptId = null; this.renderScript(); this.renderOpenSettingsDrawer("script"); this.toast("候选已加入编辑器，请点击保存台词"); }
  async rewriteScriptLine(button) {
    const projectId = this.state.project?.id;
    const scriptId = this.state.selectedScriptId;
    const requestVersion = this.requestVersion;
    const row = button.closest("[data-script-item]");
    const sequence = Number(button.dataset.lineNumber);
    const text = row?.querySelector('[name="text"]')?.value || "";
    const pronunciation = row?.querySelector('[name="pronunciation"]')?.value || "";
    const instruction = row?.querySelector('[name="rewrite_instruction"]')?.value.trim() || "";
    if (!text.trim()) { this.toast("当前台词不能为空", true); return; }
    if (!instruction) { this.toast("请输入这一行的修改要求", true); row?.querySelector('[name="rewrite_instruction"]')?.focus(); return; }
    this.captureScriptItemDraft(row);
    const form = byId("scriptItemsForm");
    if (form && this.scriptItemsNeedSave(form) && !await this.saveScriptItems(form, { notify: false, rerender: false })) return;
    if (!await this.persistCharacterContext() || !this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    const version = this.state.scriptDetail?.version;
    const payload = { kind: "rewrite_line", sequence, text, pronunciation, instruction };
    if (Number.isInteger(version)) payload.version = version;
    const parent = this.textRunParent("script", scriptId);
    if (parent) payload.parent_run_id = parent.id;
    const { run } = await this.api.post(`/api/scripts/${enc(scriptId)}/text-generation-runs`, payload); // Compatibility API still exposes the `rewrite-line` route.
    if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return;
    this.upsertTextRun(run);
    this.state.textRunParentId = null;
    this.renderScript();
    this.watchTextRun(run.id);
    this.toast(`第 ${sequence} 行已加入修改队列，可在记录中查看进度`);
  }
  adoptLineRewrite(button) {
    const row = button.closest("[data-script-item]");
    const sequence = Number(button.dataset.lineNumber);
    const key = this.lineRewriteKey(this.state.selectedScriptId, sequence);
    const suggestion = this.state.lineRewriteSuggestions[key];
    if (!row || !suggestion) return;
    row.querySelector('[name="text"]').value = suggestion.text;
    row.querySelector('[name="pronunciation"]').value = suggestion.pronunciation;
    this.captureScriptItemDraft(row);
    delete this.state.lineRewriteSuggestions[key];
    row.querySelector(".line-rewrite-result").innerHTML = "";
    this.toast(`第 ${sequence} 行已采用修改，请保存台词`);
  }
  discardLineRewrite(button) {
    const row = button.closest("[data-script-item]");
    const sequence = Number(button.dataset.lineNumber);
    delete this.state.lineRewriteSuggestions[this.lineRewriteKey(this.state.selectedScriptId, sequence)];
    if (row) row.querySelector(".line-rewrite-result").innerHTML = "";
  }
  async addMember(form) { const projectId = this.state.project?.id; const requestVersion = this.requestVersion; await this.api.post(`/api/projects/${enc(projectId)}/members`, { username: form.username.value.trim() }); if (!this.isCurrentProjectRequest(projectId, requestVersion)) return; form.reset(); await this.refreshProjectAndMembers(projectId, requestVersion); if (this.isCurrentProjectRequest(projectId, requestVersion)) this.toast("成员已添加"); }
  async removeMember(memberId) { const projectId = this.state.project?.id; const requestVersion = this.requestVersion; if (!window.confirm("确定移除该成员吗？")) return; await this.api.delete(`/api/projects/${enc(projectId)}/members/${enc(memberId)}`); if (!this.isCurrentProjectRequest(projectId, requestVersion)) return; await this.refreshProjectAndMembers(projectId, requestVersion); if (this.isCurrentProjectRequest(projectId, requestVersion)) this.toast("成员已移除"); }
  async updateMemberRole(memberId, role) { const projectId = this.state.project?.id; const requestVersion = this.requestVersion; await this.api.patch(`/api/projects/${enc(projectId)}/members/${enc(memberId)}`, { role }); if (!this.isCurrentProjectRequest(projectId, requestVersion)) return; await this.loadMembers(projectId); if (this.isCurrentProjectRequest(projectId, requestVersion)) this.toast("成员角色已更新"); }
  async refreshProjectAndMembers(projectId = this.state.project?.id, requestVersion = this.requestVersion) { const { project } = await this.api.get(`/api/projects/${enc(projectId)}`); if (!this.isCurrentProjectRequest(projectId, requestVersion)) return; this.state.project = project; this.merge(this.state.projects, project); this.renderProject(); }
  async deleteScript() { const projectId = this.state.project?.id; const scriptId = this.state.selectedScriptId; const requestVersion = this.requestVersion; const script = this.state.scripts.find((item) => item.id === scriptId); if (!script || !window.confirm(`确定删除台本“${script.name}”吗？`)) return; this.closeSettingsDrawer({ restoreFocus: false }); await this.api.delete(`/api/scripts/${enc(script.id)}`); if (!this.isCurrentScriptRequest(projectId, scriptId, requestVersion)) return; this.state.scripts = this.state.scripts.filter((item) => item.id !== script.id); this.state.selectedScriptId = this.state.scripts[0]?.id || null; this.state.scriptDetail = null; this.storePosition(); this.render(); this.toast("台本已删除"); }
  merge(items, value) { const index = items.findIndex((item) => item.id === value.id); if (index >= 0) items[index] = { ...items[index], ...value }; }
  async runBusy(button, operation) {
    if (!button) return operation();
    setButtonBusy(button, true);
    try { return await operation(); } finally { setButtonBusy(button, false); }
  }
  setSmartScriptImportMessage(message = "", error = false) {
    const element = byId("smartScriptImportMessage");
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("error", error);
    element.classList.toggle("is-progress", Boolean(message) && !error);
    element.setAttribute("role", error ? "alert" : "status");
  }
  openDialog(id) { if (id === "smartScriptImportDialog") this.setSmartScriptImportMessage(); byId(id).showModal(); }
  toast(message, error = false) { const element = byId("toast"); element.textContent = message; element.classList.toggle("error", error); element.classList.add("show"); clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => element.classList.remove("show"), 3000); }
}

new WorkstationApp().start();
