import { ApiClient } from "./core/api-client.js";
import { AuthController } from "./auth/auth-controller.js?v=20260825.1";
import { escapeHtml, setButtonBusy } from "./core/dom.js";
import { safeResourceUrl } from "./core/url.js?v=20260826.1";
import { GenerationConfigurationController } from "./generation/configuration-controller.js?v=20260826.1";

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
    this.state = { user: null, project: null, projects: [], scripts: [], voices: [], jobs: [], config: { models: [] }, selectedScriptId: null, selectedVoiceId: null, selectedModelId: null, generationConfigurations: [], generationRequest: null, currentView: "script", projectPromptSuggestion: "", projectPromptDraft: null, scriptPromptSuggestion: "", scriptPromptDraft: null, generatedLines: [], generatedLinesScriptId: null, lineRewriteSuggestions: {}, lineRewriteInstructions: {}, scriptItemDrafts: {}, scriptSearchQuery: "", voiceSearchQuery: "", settingsDrawer: null, assetRename: null, jobDetails: {} };
    this.auth = new AuthController(this.api, "appShell", (user) => this.boot(user));
    this.generation = new GenerationConfigurationController(this.state, {
      storePosition: () => this.storePosition(),
      toast: (message, error = false) => this.toast(message, error),
    });
    this.requestVersion = 0;
    this.toastTimer = null;
    this.loadingJobIds = new Set();
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
    if (!id) { this.state.project = null; this.state.scripts = []; this.state.voices = []; this.state.jobs = []; this.render(); return; }
    const previousProjectId = this.state.project?.id || null;
    const savedPosition = this.restoreProjectPosition(id);
    const restoredView = initial ? this.restoreView() : "script";
    const version = ++this.requestVersion;
    try {
      const [projectResult, scriptsResult, voicesResult, jobsResult] = await Promise.all([
        this.api.get(`/api/projects/${enc(id)}`),
        this.api.get(`/api/scripts?project_id=${enc(id)}`),
        this.api.get(`/api/voices?project_id=${enc(id)}`),
        this.api.get(`/api/jobs?limit=300&project_id=${enc(id)}`),
      ]);
      if (version !== this.requestVersion) return;
      this.state.project = projectResult.project;
      this.state.scripts = scriptsResult.scripts;
      this.state.voices = voicesResult.voices;
      this.state.jobs = jobsResult.jobs;
      if (previousProjectId !== id) {
        this.closeSettingsDrawer({ restoreFocus: false });
        this.state.scriptSearchQuery = "";
        this.state.voiceSearchQuery = "";
        this.state.assetRename = null;
        this.state.scriptDetail = null;
        this.state.voiceDetail = null;
        this.state.jobDetails = {};
        this.state.generationConfigurations = [];
        this.state.projectPromptSuggestion = "";
        this.state.projectPromptDraft = null;
        this.state.scriptPromptSuggestion = "";
        this.state.scriptPromptDraft = null;
        this.state.generatedLines = [];
        this.state.generatedLinesScriptId = null;
        this.state.lineRewriteSuggestions = {};
        this.state.lineRewriteInstructions = {};
        this.state.scriptItemDrafts = {};
      }
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
    } catch (error) { this.toast(error.message, true); }
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

  async loadScriptDetail(id) {
    if (this.state.scriptDetail?.id === id || this.loadingScript === id) return;
    this.loadingScript = id;
    try { const { script } = await this.api.get(`/api/scripts/${enc(id)}`); if (this.state.selectedScriptId === id) { this.state.scriptDetail = script; this.renderScript(); this.renderOpenSettingsDrawer("script"); } } catch (error) { this.toast(error.message, true); } finally { this.loadingScript = null; }
  }

  scriptDetail(script) {
    const detail = this.state.scriptDetail?.id === script.id ? this.state.scriptDetail : null;
    if (!detail) return '<div class="empty-panel">正在读取台本...</div>';
    const models = this.state.config.models.filter((model) => model.available === true);
    const voices = this.state.voices.filter((voice) => voice.enabled_file_count);
    const canGenerate = Boolean(voices.length && models.length && detail.items.length);
    const generationJobs = this.generationJobsForScript(detail.id);
    const selections = detail.selections || [];
    const selectedSequences = new Set(selections.map((row) => Number(row.sequence)));
    const selectedCount = detail.items.filter((item, index) => selectedSequences.has(Number(item.order || index + 1))).length;
    const lines = detail.items.map((item, index) => this.scriptLine(detail.id, item, index, canGenerate)).join("");
    const generationStatus = `${canGenerate ? `${detail.items.length} 行可生成 · 支持逐条配置` : "需要先添加可用声音和模型"}${generationJobs.length ? ` · ${generationJobs.length} 条生成记录` : ""} · 已采纳 ${selectedCount}/${detail.items.length}`;
    return `<div class="detail-inner"><header class="detail-header asset-detail-header"><div><span class="eyebrow">SCRIPT DETAIL</span><h2>${escapeHtml(detail.name)}</h2><p>${detail.item_count} 行台词 · ${escapeHtml(detail.original_name)}</p></div><div class="detail-actions asset-detail-actions"><div class="asset-detail-generation"><button class="button button-primary button-small" type="button" data-action="generate-all"${canGenerate ? "" : " disabled"} aria-label="生成全部音频" title="生成全部音频">全部生成</button><small class="generation-help" title="${escapeHtml(generationStatus)}">${escapeHtml(generationStatus)}</small></div><button class="button button-quiet button-small asset-settings-trigger" type="button" data-action="open-settings-drawer" data-kind="script" aria-haspopup="dialog"><span aria-hidden="true">⚙</span><span>设置</span></button></div></header><form id="scriptItemsForm" class="script-editor"><div class="editor-toolbar"><div><h3>台词与发音</h3><p>编辑内容、生成音频并试听采纳；修改后点击右下角保存。</p></div></div><div class="script-lines">${lines}</div><button class="button button-primary script-save-floating" type="submit" aria-label="保存全部台词与发音">保存台词</button></form></div>`;
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
    const exportAccepted = canExportAccepted
      ? '<button class="button button-quiet" type="button" data-action="export-script-audio" data-scope="accepted">导出已采纳音频</button>'
      : `<button class="button button-quiet" type="button" disabled>已采纳 ${selectedCount}/${detail.items.length}</button>`;
    const exportAll = canExportAll
      ? '<button class="button button-quiet" type="button" data-action="export-script-audio" data-scope="all">导出全部音频历史</button>'
      : '<button class="button button-quiet" type="button" disabled>暂无可导出历史</button>';
    return `<div class="settings-drawer-shell"><header class="settings-drawer-header"><div><span class="eyebrow">台本</span><h2 id="assetSettingsTitle">台本设置</h2><p>重命名、角色配置与资产管理</p></div><button class="icon-button" type="button" data-action="close-settings-drawer" title="关闭设置" aria-label="关闭设置">×</button></header><div class="settings-drawer-body"><form id="scriptSettingsForm" class="settings-section"><div class="settings-section-heading"><h3>台本信息</h3><p>在设置中修改台本名称。</p></div><label class="field"><span>台本 / 角色名称</span><input name="name" value="${escapeHtml(detail.name)}" maxlength="80" required></label><div class="form-actions"><button class="button button-primary" type="submit">保存名称</button></div></form><form id="textGenerationForm" class="settings-section"><div class="settings-section-heading"><h3>AI 生成台词</h3><p>长期角色特性会用于新台词和单行修改。</p></div><label class="field"><span>角色台词特性</span><textarea name="prompt" maxlength="12000" placeholder="例如：说话克制、句子短，不主动解释情绪；遇到质疑时先停顿，再用事实回应。">${escapeHtml(this.state.scriptPromptDraft ?? detail.prompt ?? "")}</textarea><small>生成前自动保存；AI 候选不会直接覆盖现有台词。</small></label><div class="character-context-actions"><button class="button button-quiet" type="button" data-action="suggest-script-prompt">让 AI 完善角色特性</button><button class="button button-quiet" type="button" data-action="save-character-context">保存角色特性</button></div>${this.promptSuggestion(this.state.scriptPromptSuggestion, "script")}<div class="text-generation-controls"><label class="field"><span>句数</span><input name="line_count" type="number" min="1" max="100" value="5"></label><button class="button button-primary" type="submit">生成台词候选</button></div>${pendingLines.length ? this.generatedLinesPanel(pendingLines) : ""}</form><section class="settings-section settings-asset-actions"><div class="settings-section-heading"><h3>导出与清理</h3><p>这些操作不会改变主编辑区中的台词内容。</p></div><div class="settings-action-grid"><a class="button button-quiet" href="/api/scripts/${enc(detail.id)}/export">导出台词 CSV</a>${exportAccepted}${exportAll}<button class="button button-danger" type="button" data-action="delete-all-generations"${deletableJobs.length ? "" : " disabled"}>删除全部生成历史</button></div><button class="button button-danger settings-delete-asset" type="button" data-action="delete-script">删除当前台本</button></section></div></div>`;
  }

  promptSuggestion(suggestion, scope) {
    if (!suggestion) return "";
    return `<section class="prompt-suggestion"><div><strong>AI 建议草稿</strong><small>当前内容不会自动被替换。</small></div><pre>${escapeHtml(suggestion)}</pre><button class="button button-quiet button-small" type="button" data-action="adopt-${scope}-prompt">采用建议（仍需保存）</button></section>`;
  }

  generatedLinesPanel(lines) {
    return `<section class="generated-lines-panel"><header><strong>台词候选</strong><small>${lines.length} 句 · 尚未加入台本</small></header><ol>${lines.map((line) => `<li>${escapeHtml(line.text)}</li>`).join("")}</ol><button class="button button-quiet button-small" type="button" data-action="adopt-generated-lines">确认并加入编辑器</button></section>`;
  }

  scriptLine(scriptId, item, index, canGenerate) {
    const sequence = Number(item.order || index + 1);
    const draft = this.state.scriptItemDrafts[scriptId]?.[sequence];
    const text = draft?.text ?? item.text;
    const pronunciation = draft?.pronunciation ?? item.pronunciation;
    const rewriteInstruction = this.state.lineRewriteInstructions[this.lineRewriteKey(scriptId, sequence)] || "";
    return `<div class="script-line" data-script-item data-line-number="${sequence}"><div class="line-heading"><span class="line-number">${String(sequence).padStart(2, "0")}</span><div class="line-actions"><button class="button button-quiet button-small" type="button" data-action="generate-line" data-line-number="${sequence}"${canGenerate ? "" : " disabled"}>生成音频</button></div></div><div class="line-fields"><label><span>台词</span><textarea name="text" maxlength="2000">${escapeHtml(text)}</textarea></label><label><span>发音</span><textarea name="pronunciation" maxlength="2000">${escapeHtml(pronunciation)}</textarea></label></div><div class="line-rewrite-controls"><label><span>AI 单行修改要求</span><input name="rewrite_instruction" maxlength="4000" value="${escapeHtml(rewriteInstruction)}" placeholder="例如：更克制、缩短到 20 字以内，不改变事实。"></label><button class="button button-quiet button-small" type="button" data-action="rewrite-line" data-line-number="${sequence}">生成修改候选</button></div><div class="line-rewrite-result">${this.lineRewriteSuggestion(scriptId, sequence)}</div>${this.lineResult(scriptId, sequence)}</div>`;
  }

  lineRewriteKey(scriptId, sequence) {
    return `${scriptId}:${sequence}`;
  }

  lineRewriteSuggestion(scriptId, sequence) {
    const suggestion = this.state.lineRewriteSuggestions[this.lineRewriteKey(scriptId, sequence)];
    if (!suggestion) return "";
    return `<section class="line-rewrite-suggestion"><header><strong>AI 修改候选</strong><small>尚未替换当前行</small></header><div class="line-rewrite-preview"><div><span>台词</span><p>${escapeHtml(suggestion.text)}</p></div><div><span>发音</span><p>${escapeHtml(suggestion.pronunciation)}</p></div></div><div class="line-rewrite-actions"><button class="button button-primary button-small" type="button" data-action="adopt-line-rewrite" data-line-number="${sequence}">采用修改</button><button class="button button-quiet button-small" type="button" data-action="discard-line-rewrite" data-line-number="${sequence}">放弃</button></div></section>`;
  }

  lineResult(scriptId, sequence) {
    const jobs = this.generationJobsForScript(scriptId);
    const selection = (this.state.scriptDetail?.selections || []).find((row) => Number(row.sequence) === sequence);
    const records = [];
    for (const job of jobs) {
      const detail = this.state.jobDetails[job.id];
      const item = detail?.items?.find((entry) => Number(entry.sequence) === sequence);
      if (item) records.push(this.renderLineHistory(job, item, selection));
      else if (!detail && Number(job.total_items) > 1) records.push(this.renderLineHistory(job, null, selection));
    }
    if (!records.length) return "";
    return `<section class="line-history" aria-label="第 ${sequence} 行生成历史"><div class="line-history-heading"><span>生成历史</span><small>${records.length} 条</small></div><div class="line-history-grid">${records.join("")}</div></section>`;
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
      : `<button class="button button-primary button-small" type="button" data-action="select-generation" data-job-id="${escapeHtml(job.id)}" data-item-id="${escapeHtml(item.id)}" data-candidate-id="${escapeHtml(candidate.id)}" data-sequence="${escapeHtml(item.sequence)}"${candidate.status === "completed" ? "" : " disabled"}>采纳</button>`;
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
    try { const { voice } = await this.api.get(`/api/voices/${enc(id)}`); if (this.state.selectedVoiceId === id) { this.state.voiceDetail = voice; this.renderVoice(); this.renderOpenSettingsDrawer("voice"); } } catch (error) { this.toast(error.message, true); } finally { this.loadingVoice = null; }
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
    try {
      const { job } = await this.api.get(`/api/jobs/${enc(id)}`);
      this.state.jobDetails[id] = job;
      if (this.state.selectedScriptId === job.script_id && !document.activeElement?.closest?.("form")) this.renderScript();
    } catch (error) { this.toast(error.message, true); } finally { this.loadingJobIds.delete(id); }
  }

  renderProject() {
    const project = this.state.project;
    const canManage = project.can_manage;
    byId("projectContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">项目管理</span><h1>项目资料与成员</h1><p>管理项目范围、共享上下文和参与成员。</p></div></div><div class="project-layout"><section class="project-section work-section"><header class="section-heading"><div><h2>项目资料</h2><p>更新名称、说明和所有角色共用的项目级上下文</p></div></header>${canManage ? `<form id="projectSettingsForm" class="project-settings-form"><label class="field"><span>项目名称</span><input name="name" value="${escapeHtml(project.name)}" maxlength="80" required></label><label class="field"><span>项目说明</span><textarea name="description" maxlength="500">${escapeHtml(project.description)}</textarea></label><label class="field"><span>项目级上下文</span><textarea name="prompt" maxlength="12000" placeholder="统一定义世界观、角色关系、语言风格、输出格式和禁用项。">${escapeHtml(this.state.projectPromptDraft ?? project.prompt ?? "")}</textarea><small>所有角色都会继承这段上下文；每个角色可再保存自己的台词特性。</small></label><div class="form-actions"><button class="button button-quiet" type="button" data-action="suggest-project-prompt">让 AI 完善上下文</button><button class="button button-primary" type="submit">保存资料</button></div>${this.promptSuggestion(this.state.projectPromptSuggestion, "project")}</form>` : `<div class="section-body"><p>${escapeHtml(project.description || "暂无项目说明")}</p><div class="readonly-prompt"><strong>项目级上下文</strong><p>${escapeHtml(project.prompt || "尚未设置")}</p></div></div>`}</section><section class="project-section work-section"><header class="section-heading"><div><h2>成员</h2><p>${project.member_count} 位项目成员</p></div></header><div id="memberList" class="member-list"><p class="empty-list">正在读取成员...</p></div>${canManage ? '<form id="memberAddForm" class="member-add"><span>按用户名添加已由系统管理员创建的账户。</span><div class="inline-form"><input name="username" maxlength="64" required placeholder="member@example.com"><button class="button button-quiet" type="submit">添加成员</button></div></form>' : ""}<p class="project-id-note">PROJECT ID · ${escapeHtml(project.id)}</p></section></div>`;
    void this.loadMembers(project.id);
  }

  async loadMembers(id) {
    if (this.loadingMembers === id) return;
    this.loadingMembers = id;
    try { const { members } = await this.api.get(`/api/projects/${enc(id)}/members`); if (this.state.project?.id === id) this.renderMembers(members); } catch (error) { this.toast(error.message, true); } finally { this.loadingMembers = null; }
  }

  renderMembers(members) {
    const canManage = this.state.project?.can_manage;
    const target = byId("memberList");
    if (!target) return;
    target.innerHTML = members.map((member) => `<div class="member-row"><span class="member-avatar">${escapeHtml(first(member.display_name || member.username))}</span><span class="member-copy"><strong>${escapeHtml(member.display_name)}</strong><small>@${escapeHtml(member.username)}</small></span>${canManage && member.role !== "owner" && member.id !== this.state.user.id ? `<select data-action="member-role" data-member-id="${escapeHtml(member.id)}"><option value="member"${member.role === "member" ? " selected" : ""}>成员</option><option value="admin"${member.role === "admin" ? " selected" : ""}>项目管理员</option></select><button class="icon-button" type="button" title="移除成员" data-action="remove-member" data-member-id="${escapeHtml(member.id)}">×</button>` : `<span class="member-role-label">${roleLabel(member.role)}</span><span></span>`}</div>`).join("");
  }

  async refreshJobs() {
    const projectId = this.state.project?.id;
    const hasActiveJobs = this.state.jobs.some((job) => ["queued", "running"].includes(job.status));
    if (!projectId || !hasActiveJobs || this.loadingJobs || document.activeElement?.closest?.("form")) return;
    this.loadingJobs = true;
    try {
      const { jobs } = await this.api.get(`/api/jobs?limit=300&project_id=${enc(projectId)}`);
      if (this.state.project?.id !== projectId) return;
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
    const name = form.name.value.trim();
    if (!name) return;
    if (kind === "script") {
      const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, { name });
      this.merge(this.state.scripts, script);
      if (this.state.scriptDetail?.id === id) this.state.scriptDetail = { ...this.state.scriptDetail, ...script };
    } else {
      const voice = this.state.voices.find((item) => item.id === id);
      const notes = voice?.notes ?? this.state.voiceDetail?.notes ?? "";
      const { voice: updated } = await this.api.patch(`/api/voices/${enc(id)}`, { name, notes });
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
      if (action === "open-voice") this.openDialog("voiceDialog");
      if (action === "open-settings-drawer") this.openSettingsDrawer(button.dataset.kind);
      if (action === "close-settings-drawer") this.closeSettingsDrawer();
      if (action === "refresh") await this.selectProject(this.state.project?.id || "", true);
      if (action === "select-script") { this.closeSettingsDrawer({ restoreFocus: false }); this.state.selectedScriptId = button.dataset.id; this.state.scriptDetail = null; this.state.scriptPromptSuggestion = ""; this.state.scriptPromptDraft = null; this.state.generatedLines = []; this.state.generatedLinesScriptId = null; this.storePosition(); this.renderScript(); }
      if (action === "select-voice") { this.closeSettingsDrawer({ restoreFocus: false }); this.state.selectedVoiceId = button.dataset.id; this.state.voiceDetail = null; this.storePosition(); this.renderVoice(); this.renderScript(); }
      if (action === "rewrite-line") await this.runBusy(button, () => this.rewriteScriptLine(button));
      if (action === "adopt-line-rewrite") this.adoptLineRewrite(button);
      if (action === "discard-line-rewrite") this.discardLineRewrite(button);
      if (action === "select-generation") await this.selectGeneration(button.dataset);
      if (action === "clear-generation-selection") await this.clearGenerationSelection(Number(button.dataset.sequence));
      if (action === "export-script-audio") this.exportScriptAudio(button.dataset.scope);
      if (action === "delete-generation") await this.deleteGeneration(button.dataset.jobId, button.dataset.itemId);
      if (action === "delete-all-generations") await this.deleteAllGenerations();
      if (action === "delete-script") await this.deleteScript();
      if (action === "remove-member") await this.removeMember(button.dataset.memberId);
      if (action === "suggest-project-prompt") await this.runBusy(button, () => this.suggestProjectPrompt());
      if (action === "suggest-script-prompt") await this.runBusy(button, () => this.suggestScriptPrompt());
      if (action === "save-character-context") await this.runBusy(button, () => this.saveCharacterContext());
      if (action === "adopt-project-prompt") this.adoptProjectPrompt();
      if (action === "adopt-script-prompt") this.adoptScriptPrompt();
      if (action === "adopt-generated-lines") this.adoptGeneratedLines();
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
    if (["text", "pronunciation"].includes(input.name) && input.closest("[data-script-item]")) {
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
    };
  }

  async change(event) {
    const input = event.target;
    try {
      if (input.dataset.action === "file-enabled") await this.updateVoiceFile(input.dataset.fileId, { enabled: input.checked });
      if (input.dataset.action === "file-emotion") await this.updateVoiceFile(input.dataset.fileId, { emotion_tag: input.value });
      if (input.dataset.action === "member-role") await this.updateMemberRole(input.dataset.memberId, input.value);
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
    } catch (error) { this.toast(error.message, true); }
  }

  async createProject(form) { const { project } = await this.api.post("/api/projects", { name: form.name.value.trim(), description: form.description.value.trim() }); this.state.projects.push(project); form.reset(); byId("projectDialog").close(); await this.selectProject(project.id, true); this.showView("script"); this.toast("项目已创建"); }
  async createScript(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { script } = await this.api.postForm("/api/scripts", data); this.state.scripts.unshift(script); this.state.selectedScriptId = script.id; this.state.scriptDetail = null; form.reset(); byId("scriptUploadFileName").textContent = "选择台本文件"; byId("scriptDialog").close(); this.render(); this.showView("script"); this.toast("台本已导入"); }
  async createVoice(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { voice } = await this.api.postForm("/api/voices", data); this.state.voices.unshift(voice); this.state.selectedVoiceId = voice.id; this.state.voiceDetail = null; form.reset(); byId("voiceFileName").textContent = "选择参考录音"; byId("voiceDialog").close(); this.render(); this.showView("voice"); this.toast("声音已创建"); }
  async saveScriptSettings(form) { const id = this.state.selectedScriptId; const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, { name: form.name.value.trim(), prompt: this.currentCharacterContext() }); this.merge(this.state.scripts, script); this.state.scriptDetail = { ...this.state.scriptDetail, ...script }; this.render(); this.toast("名称已保存"); }
  async saveScriptItems(form, options = {}) { const id = this.state.selectedScriptId; const items = [...form.querySelectorAll("[data-script-item]")].map((row) => ({ text: row.querySelector('[name="text"]').value, pronunciation: row.querySelector('[name="pronunciation"]').value })); const { script } = await this.api.put(`/api/scripts/${enc(id)}/items`, { items }); this.state.scriptDetail = script; this.merge(this.state.scripts, script); delete this.state.scriptItemDrafts[id]; Object.keys(this.state.lineRewriteSuggestions).filter((key) => key.startsWith(`${id}:`)).forEach((key) => delete this.state.lineRewriteSuggestions[key]); if (options.rerender !== false) this.renderScript(); if (options.notify !== false) this.toast("台词与发音已保存"); return script; }
  async saveVoiceSettings(form) { const id = this.state.selectedVoiceId; const { voice } = await this.api.patch(`/api/voices/${enc(id)}`, { name: form.name.value.trim(), notes: form.notes.value.trim() }); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.render(); this.toast("声音信息已保存"); }
  async appendVoiceFiles(form) { const { voice } = await this.api.postForm(`/api/voices/${enc(this.state.selectedVoiceId)}/files`, new FormData(form)); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); form.reset(); this.renderVoiceSettingsFiles(); this.renderVoice(); this.toast("参考录音已追加"); }
  async updateVoiceFile(fileId, changes) { const { voice } = await this.api.patch(`/api/voices/${enc(this.state.selectedVoiceId)}/files/${enc(fileId)}`, changes); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.renderVoiceSettingsFiles(); this.renderVoice(); this.toast("录音设置已更新"); }
  async submitGenerationOptions() {
    const submission = this.generation.submission();
    if (!submission) return;
    if (!await this.generateScript(submission.lineNumber, submission.configurations)) return;
    this.generation.finish();
  }

  async generateScript(lineNumber = null, configurations = []) {
    const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const voices = this.state.voices.filter((item) => item.enabled_file_count);
    const models = this.state.config.models.filter((model) => model.available === true);
    const validConfigurations = configurations.filter((configuration) => voices.some((voice) => voice.id === configuration.voiceId) && models.some((model) => model.id === configuration.modelId));
    if (!script || !validConfigurations.length || validConfigurations.length !== configurations.length) { this.toast("请检查生成配置中的原声和模型", true); return false; }
    const form = byId("scriptItemsForm");
    if (form) await this.saveScriptItems(form, { notify: false, rerender: false });
    const results = await Promise.all(validConfigurations.map((configuration) => {
      const data = new FormData();
      data.set("project_id", this.state.project.id);
      data.set("script_id", script.id);
      data.set("voice_id", configuration.voiceId);
      data.set("model_id", configuration.modelId);
      data.set("candidate_count", String(Math.min(4, Math.max(1, Number(configuration.candidateCount) || 1))));
      data.set("reference_emotion", "all");
      data.set("generation_settings", "{}");
      data.set("name", lineNumber === null ? script.name : `${script.name} · 第 ${lineNumber} 行`);
      if (lineNumber !== null) data.set("line_number", String(lineNumber));
      return this.api.postForm("/api/jobs", data);
    }));
    const jobs = results.flatMap((result) => result.jobs || [result.job]);
    this.state.jobs.unshift(...jobs);
    for (const job of jobs) this.state.jobDetails[job.id] = null;
    this.renderScript();
    const activeJobs = this.state.jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    byId("queueSummary").textContent = `${activeJobs} 个处理中`;
    byId("workerDot").className = "online";
    const candidateCount = validConfigurations.reduce((total, configuration) => total + configuration.candidateCount, 0);
    this.toast(lineNumber === null ? `全部台词已加入生成队列（${jobs.length} 个任务，每行 ${candidateCount} 条候选）` : `第 ${lineNumber} 行已加入生成队列（${jobs.length} 个任务，共 ${candidateCount} 条候选）`);
    for (const job of jobs) void this.loadJobDetail(job.id, true);
    return true;
  }
  async selectGeneration(data) {
    const scriptId = this.state.selectedScriptId;
    const result = await this.api.post(`/api/scripts/${enc(scriptId)}/selections`, {
      sequence: Number(data.sequence),
      job_id: data.jobId,
      item_id: data.itemId,
      candidate_id: data.candidateId,
    });
    const detail = this.state.scriptDetail;
    if (detail) {
      detail.selections = [...(detail.selections || []).filter((row) => Number(row.sequence) !== Number(data.sequence)), result.selection];
    }
    this.renderScript();
    this.toast(`第 ${data.sequence} 行已标记为采纳`);
  }
  async clearGenerationSelection(sequence) {
    await this.api.delete(`/api/scripts/${enc(this.state.selectedScriptId)}/selections/${enc(sequence)}`);
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
    const job = this.state.jobs.find((item) => item.id === jobId);
    if (!job || !itemId) return;
    const detail = this.state.jobDetails[jobId];
    const item = detail?.items?.find((entry) => entry.id === itemId);
    if (["queued", "running"].includes(item?.status || job.status)) return;
    if (!window.confirm("确定删除这条生成结果吗？对应音频文件也会被删除。")) return;
    await this.api.delete(`/api/jobs/${enc(jobId)}/items/${enc(itemId)}`);
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
    this.renderScript();
    this.toast("生成结果已删除");
  }
  async deleteAllGenerations() {
    const scriptId = this.state.selectedScriptId;
    const jobs = this.generationJobsForScript(scriptId).filter((job) => !["queued", "running"].includes(job.status));
    if (!jobs.length) return;
    if (!window.confirm(`确定删除当前台本的 ${jobs.length} 个生成结果吗？对应音频文件也会被删除。`)) return;
    for (const job of jobs) await this.api.delete(`/api/jobs/${enc(job.id)}`);
    const deletedIds = new Set(jobs.map((job) => job.id));
    this.state.jobs = this.state.jobs.filter((job) => !deletedIds.has(job.id));
    if (this.state.scriptDetail) this.state.scriptDetail.selections = (this.state.scriptDetail.selections || []).filter((entry) => !deletedIds.has(entry.job_id));
    for (const job of jobs) delete this.state.jobDetails[job.id];
    this.renderScript();
    this.renderOpenSettingsDrawer("script");
    this.toast(`${jobs.length} 个生成结果已删除`);
  }
  async saveProject(form) { const { project } = await this.api.patch(`/api/projects/${enc(this.state.project.id)}`, { name: form.name.value.trim(), description: form.description.value.trim(), prompt: form.prompt.value }); this.state.project = project; this.state.projectPromptSuggestion = ""; this.state.projectPromptDraft = null; this.merge(this.state.projects, project); this.render(); this.toast("项目资料已保存"); }
  async suggestProjectPrompt() { const form = byId("projectSettingsForm"); this.state.projectPromptDraft = form?.prompt?.value || ""; const { suggestion } = await this.api.post(`/api/projects/${enc(this.state.project.id)}/prompt-suggestion`, { goal: this.state.projectPromptDraft }); this.state.projectPromptSuggestion = suggestion; this.renderProject(); this.toast("已生成项目提示词建议"); }
  adoptProjectPrompt() { const form = byId("projectSettingsForm"); if (!form || !this.state.projectPromptSuggestion) return; this.state.projectPromptDraft = this.state.projectPromptSuggestion; form.prompt.value = this.state.projectPromptDraft; this.toast("建议已放入编辑框，请保存"); }
  currentCharacterContext() { return byId("textGenerationForm")?.prompt?.value ?? this.state.scriptPromptDraft ?? this.state.scriptDetail?.prompt ?? ""; }
  async persistCharacterContext(options = {}) { const id = this.state.selectedScriptId; const name = this.state.scriptDetail?.name || ""; const prompt = this.currentCharacterContext(); const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, { name, prompt }); this.merge(this.state.scripts, script); this.state.scriptDetail = { ...this.state.scriptDetail, ...script }; this.state.scriptPromptDraft = null; if (options.clearSuggestion) this.state.scriptPromptSuggestion = ""; return script; }
  async saveCharacterContext() { await this.persistCharacterContext({ clearSuggestion: true }); this.renderOpenSettingsDrawer("script"); this.toast("角色台词特性已保存"); }
  async suggestScriptPrompt() { this.state.scriptPromptDraft = this.currentCharacterContext(); const { suggestion } = await this.api.post(`/api/scripts/${enc(this.state.selectedScriptId)}/prompt-suggestion`, { goal: this.state.scriptPromptDraft }); this.state.scriptPromptSuggestion = suggestion; this.renderOpenSettingsDrawer("script"); this.toast("已生成角色台词特性建议"); }
  adoptScriptPrompt() { const form = byId("textGenerationForm"); if (!form || !this.state.scriptPromptSuggestion) return; this.state.scriptPromptDraft = this.state.scriptPromptSuggestion; form.prompt.value = this.state.scriptPromptDraft; this.toast("建议已放入角色特性框，请保存"); }
  async generateText(form) { await this.persistCharacterContext(); const { lines } = await this.api.post(`/api/scripts/${enc(this.state.selectedScriptId)}/generate-text`, { instruction: "", line_count: Number(form.line_count.value) }); this.state.generatedLines = lines; this.state.generatedLinesScriptId = this.state.selectedScriptId; this.renderOpenSettingsDrawer("script"); this.toast(`已生成 ${lines.length} 句台词候选`); }
  adoptGeneratedLines() { const detail = this.state.scriptDetail; const lines = this.state.generatedLinesScriptId === detail?.id ? this.state.generatedLines : []; if (!detail || !lines.length) return; const items = [...detail.items]; for (const line of lines) items.push({ order: items.length + 1, source_line: items.length + 1, text: line.text, pronunciation: line.pronunciation, generated_text: line.text, direction: "flat", emphasis: [], hold_units: [], raw_mode: false }); this.state.scriptDetail = { ...detail, items, item_count: items.length }; this.state.generatedLines = []; this.state.generatedLinesScriptId = null; this.renderScript(); this.renderOpenSettingsDrawer("script"); this.toast("候选已加入编辑器，请点击保存台词"); }
  async rewriteScriptLine(button) {
    const row = button.closest("[data-script-item]");
    const sequence = Number(button.dataset.lineNumber);
    const text = row?.querySelector('[name="text"]')?.value || "";
    const pronunciation = row?.querySelector('[name="pronunciation"]')?.value || "";
    const instruction = row?.querySelector('[name="rewrite_instruction"]')?.value.trim() || "";
    if (!text.trim()) { this.toast("当前台词不能为空", true); return; }
    if (!instruction) { this.toast("请输入这一行的修改要求", true); row?.querySelector('[name="rewrite_instruction"]')?.focus(); return; }
    this.captureScriptItemDraft(row);
    await this.persistCharacterContext();
    const { line } = await this.api.post(`/api/scripts/${enc(this.state.selectedScriptId)}/rewrite-line`, { sequence, text, pronunciation, instruction });
    this.state.lineRewriteSuggestions[this.lineRewriteKey(this.state.selectedScriptId, sequence)] = line;
    const target = row.querySelector(".line-rewrite-result");
    if (target) target.innerHTML = this.lineRewriteSuggestion(this.state.selectedScriptId, sequence);
    this.toast(`第 ${sequence} 行已生成修改候选，请确认`);
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
  async addMember(form) { await this.api.post(`/api/projects/${enc(this.state.project.id)}/members`, { username: form.username.value.trim() }); form.reset(); await this.refreshProjectAndMembers(); this.toast("成员已添加"); }
  async removeMember(memberId) { if (!window.confirm("确定移除该成员吗？")) return; await this.api.delete(`/api/projects/${enc(this.state.project.id)}/members/${enc(memberId)}`); await this.refreshProjectAndMembers(); this.toast("成员已移除"); }
  async updateMemberRole(memberId, role) { await this.api.patch(`/api/projects/${enc(this.state.project.id)}/members/${enc(memberId)}`, { role }); await this.loadMembers(this.state.project.id); this.toast("成员角色已更新"); }
  async refreshProjectAndMembers() { const { project } = await this.api.get(`/api/projects/${enc(this.state.project.id)}`); this.state.project = project; this.merge(this.state.projects, project); this.renderProject(); }
  async deleteScript() { const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId); if (!script || !window.confirm(`确定删除台本“${script.name}”吗？`)) return; this.closeSettingsDrawer({ restoreFocus: false }); await this.api.delete(`/api/scripts/${enc(script.id)}`); this.state.scripts = this.state.scripts.filter((item) => item.id !== script.id); this.state.selectedScriptId = this.state.scripts[0]?.id || null; this.state.scriptDetail = null; this.storePosition(); this.render(); this.toast("台本已删除"); }
  merge(items, value) { const index = items.findIndex((item) => item.id === value.id); if (index >= 0) items[index] = { ...items[index], ...value }; }
  async runBusy(button, operation) {
    if (!button) return operation();
    setButtonBusy(button, true);
    try { return await operation(); } finally { setButtonBusy(button, false); }
  }
  openDialog(id) { byId(id).showModal(); }
  toast(message, error = false) { const element = byId("toast"); element.textContent = message; element.classList.toggle("error", error); element.classList.add("show"); clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => element.classList.remove("show"), 3000); }
}

new WorkstationApp().start();
