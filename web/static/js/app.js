import { ApiClient } from "./core/api-client.js";
import { AuthController } from "./auth/auth-controller.js?v=20260825.1";
import { escapeHtml } from "./core/dom.js";

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
    this.state = { user: null, project: null, projects: [], scripts: [], voices: [], jobs: [], config: { models: [] }, selectedScriptId: null, selectedVoiceId: null, selectedModelId: null, generationVoiceIds: [], generationModelIds: [], generationRequest: null, currentView: "script", projectPromptSuggestion: "", projectPromptDraft: null, scriptPromptSuggestion: "", scriptPromptDraft: null, generatedLines: [], generatedLinesScriptId: null, scriptSearchQuery: "", voiceSearchQuery: "", jobDetails: {} };
    this.auth = new AuthController(this.api, "appShell", (user) => this.boot(user));
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
    document.addEventListener("play", (event) => this.pauseOtherAudio(event.target), true);
    byId("scriptUploadFile").addEventListener("change", (event) => { byId("scriptUploadFileName").textContent = event.target.files[0]?.name || "选择台本文件"; });
    byId("voiceFiles").addEventListener("change", (event) => { byId("voiceFileName").textContent = event.target.files.length ? `${event.target.files.length} 条参考录音` : "选择参考录音"; });
  }

  async boot(user) {
    this.state.user = user;
    byId("clientId").textContent = user.display_name || user.username;
    byId("identityRole").textContent = user.is_admin ? "系统管理员" : "项目成员";
    byId("userAvatar").textContent = first(user.display_name || user.username);
    byId("adminLink").hidden = !user.is_admin;
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
        generationVoiceIds: [...this.state.generationVoiceIds],
        generationModelIds: [...this.state.generationModelIds],
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
        this.state.scriptSearchQuery = "";
        this.state.voiceSearchQuery = "";
        this.state.scriptDetail = null;
        this.state.voiceDetail = null;
        this.state.jobDetails = {};
        this.state.generationVoiceIds = [];
        this.state.generationModelIds = [];
        this.state.projectPromptSuggestion = "";
        this.state.projectPromptDraft = null;
        this.state.scriptPromptSuggestion = "";
        this.state.scriptPromptDraft = null;
        this.state.generatedLines = [];
        this.state.generatedLinesScriptId = null;
      }
      const savedScriptId = previousProjectId === id ? this.state.selectedScriptId : savedPosition.scriptId;
      const savedVoiceId = previousProjectId === id ? this.state.selectedVoiceId : savedPosition.voiceId;
      const savedModelId = previousProjectId === id ? this.state.selectedModelId : savedPosition.modelId;
      this.state.selectedScriptId = this.state.scripts.some((item) => item.id === savedScriptId) ? savedScriptId : this.state.scripts[0]?.id || null;
      const usableVoices = this.state.voices.filter((item) => item.enabled_file_count);
      this.state.selectedVoiceId = usableVoices.some((item) => item.id === savedVoiceId) ? savedVoiceId : usableVoices[0]?.id || this.state.voices[0]?.id || null;
      const models = this.state.config.models.filter((model) => model.available === true);
      this.state.selectedModelId = models.some((model) => model.id === savedModelId) ? savedModelId : models[0]?.id || null;
      const generationVoiceIds = previousProjectId === id
        ? this.state.generationVoiceIds
        : savedPosition.generationVoiceIds;
      const generationModelIds = previousProjectId === id
        ? this.state.generationModelIds
        : savedPosition.generationModelIds;
      const usableVoiceIds = new Set(usableVoices.map((voice) => voice.id));
      const availableModelIds = new Set(models.map((model) => model.id));
      this.state.generationVoiceIds = Array.isArray(generationVoiceIds)
        ? [...new Set(generationVoiceIds)].filter((value) => usableVoiceIds.has(value))
        : [];
      this.state.generationModelIds = Array.isArray(generationModelIds)
        ? [...new Set(generationModelIds)].filter((value) => availableModelIds.has(value))
        : [];
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
  }

  showView(view) {
    if (!WORKSTATION_VIEWS.has(view)) return;
    this.state.currentView = view;
    this.storePosition();
    document.querySelectorAll("[data-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; });
    document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    byId("workspaceMain").scrollTop = 0;
  }

  renderScript(options = {}) {
    const focus = options.focusScriptId
      ? { type: "script", id: options.focusScriptId }
      : this.captureScriptFocus();
    const selected = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const query = String(this.state.scriptSearchQuery || "").trim().toLowerCase();
    const scripts = query ? this.state.scripts.filter((script) => String(script.search_text || `${script.name} ${script.original_name}`).toLowerCase().includes(query)) : this.state.scripts;
    const list = scripts.map((script) => `<button class="asset-row${script.id === selected?.id ? " active" : ""}" type="button" data-action="select-script" data-id="${escapeHtml(script.id)}" aria-current="${script.id === selected?.id}"><span class="asset-icon">T</span><span class="asset-copy"><strong>${escapeHtml(script.name)}</strong><small>${script.item_count} 行 · ${formatDate(script.created_at)}</small></span><span class="asset-status">台词</span></button>`).join("") || (query && this.state.scripts.length ? '<p class="empty-list">没有匹配的台本</p>' : '<p class="empty-list">尚未导入台本</p>');
    const detail = selected ? this.scriptDetail(selected) : '<div class="empty-panel"><div><strong>选择或导入一个台本</strong>上传后可编辑每行台词和发音。</div></div>';
    byId("scriptContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">SCRIPT WORKSTATION</span><h1>台本</h1><p>选中台本后，直接编辑台词和发音，选择声音并生成音频。</p></div></div><div class="split-workspace"><aside class="asset-sidebar"><header class="asset-sidebar-header"><div><h2>台本</h2><small>${this.state.scripts.length} 个台本</small></div><button class="button button-quiet button-small" type="button" data-action="open-script">添加</button></header><label class="asset-search" for="scriptSearch"><input id="scriptSearch" type="search" value="${escapeHtml(this.state.scriptSearchQuery)}" autocomplete="off" placeholder="搜索台本" aria-label="搜索台本"></label><div class="asset-list">${list}</div></aside><section class="detail-panel">${detail}</section></div>`;
    this.restoreScriptFocus(focus);
    if (selected) void this.loadScriptDetail(selected.id);
  }

  captureScriptFocus() {
    const active = document.activeElement;
    if (active?.id === "scriptSearch") {
      return { type: "search", start: active.selectionStart, end: active.selectionEnd };
    }
    const row = active?.closest?.('[data-action="select-script"]');
    return row ? { type: "script", id: row.dataset.id } : null;
  }

  restoreScriptFocus(focus) {
    if (!focus) return;
    if (focus.type === "script") {
      const row = [...document.querySelectorAll('[data-action="select-script"]')].find((item) => item.dataset.id === focus.id);
      row?.focus();
      return;
    }
    const search = document.querySelector("#scriptSearch");
    if (!search) return;
    search.focus({ preventScroll: true });
    if (Number.isInteger(focus.start)) search.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
  }

  async loadScriptDetail(id) {
    if (this.state.scriptDetail?.id === id || this.loadingScript === id) return;
    this.loadingScript = id;
    try { const { script } = await this.api.get(`/api/scripts/${enc(id)}`); if (this.state.selectedScriptId === id) { this.state.scriptDetail = script; this.renderScript(); } } catch (error) { this.toast(error.message, true); } finally { this.loadingScript = null; }
  }

  scriptDetail(script) {
    const detail = this.state.scriptDetail?.id === script.id ? this.state.scriptDetail : null;
    if (!detail) return '<div class="empty-panel">正在读取台本...</div>';
    const models = this.state.config.models.filter((model) => model.available === true);
    const voices = this.state.voices.filter((voice) => voice.enabled_file_count);
    const canGenerate = Boolean(voices.length && models.length && detail.items.length);
    const generationJobs = this.generationJobsForScript(detail.id);
    const deletableJobs = generationJobs.filter((job) => !["queued", "running"].includes(job.status));
    const lines = detail.items.map((item, index) => this.scriptLine(detail.id, item, index, canGenerate)).join("");
    const pendingLines = this.state.generatedLinesScriptId === detail.id ? this.state.generatedLines : [];
    return `<div class="detail-inner"><header class="detail-header"><div><span class="eyebrow">SCRIPT DETAIL</span><h2>${escapeHtml(detail.name)}</h2><p>${detail.item_count} 行台词 · ${escapeHtml(detail.original_name)}</p></div><div class="detail-actions"><a class="button button-quiet button-small" href="/api/scripts/${enc(detail.id)}/export">导出 CSV</a><button class="button button-danger button-small" type="button" data-action="delete-script">删除</button></div></header><section class="script-generation work-section"><div class="generation-copy"><span class="eyebrow">GENERATE</span><h3>生成音频</h3><p>点击生成时选择声音和模型，可一次创建多种组合。</p></div><div class="generation-controls"><div class="generation-actions"><button class="button button-primary" type="button" data-action="generate-all"${canGenerate ? "" : " disabled"}>全部生成</button><button class="button button-danger" type="button" data-action="delete-all-generations"${deletableJobs.length ? "" : " disabled"}>全部删除</button></div></div><small class="generation-help">${canGenerate ? `${detail.items.length} 行可生成 · 支持多声音和多模型组合` : "需要先添加可用声音和模型"}${generationJobs.length ? ` · 已保留 ${generationJobs.length} 条生成记录` : ""}</small></section><form id="scriptSettingsForm" class="detail-form"><label class="field"><span>台本名称</span><input name="name" value="${escapeHtml(detail.name)}" maxlength="80" required></label><label class="field wide"><span>单台本提示词</span><textarea name="prompt" maxlength="12000" placeholder="角色、场景、语气、格式和禁用项。会叠加在项目总体提示词之后。">${escapeHtml(this.state.scriptPromptDraft ?? detail.prompt ?? "")}</textarea><small>提示词由人工维护；AI 建议只生成草稿，不会自动覆盖。</small></label><div class="form-actions"><button class="button button-quiet" type="button" data-action="suggest-script-prompt">让 AI 完善提示词</button><button class="button button-primary" type="submit">保存台本设置</button></div>${this.promptSuggestion(this.state.scriptPromptSuggestion, "script")}</form><form id="textGenerationForm" class="text-generation-form"><div class="editor-toolbar"><div><h3>AI 生成台词</h3><p>使用项目总体提示词 + 单台本提示词生成草稿，确认后再加入编辑器。</p></div></div><label class="field wide"><span>本次要求</span><textarea name="instruction" maxlength="4000" placeholder="例如：写 5 句，表现角色第一次发现异常时的克制惊讶。"></textarea></label><div class="text-generation-controls"><label class="field"><span>句数</span><input name="line_count" type="number" min="1" max="100" value="5"></label><button class="button button-primary" type="submit">生成台词草稿</button></div>${pendingLines.length ? this.generatedLinesPanel(pendingLines) : ""}</form><form id="scriptItemsForm" class="script-editor"><div class="editor-toolbar"><div><h3>台词与发音</h3><p>每一行都可以单独编辑和生成。</p></div><button class="button button-primary button-small" type="submit">保存行内容</button></div><div class="script-lines">${lines}</div></form></div>`;
  }

  promptSuggestion(suggestion, scope) {
    if (!suggestion) return "";
    return `<section class="prompt-suggestion"><div><strong>AI 建议草稿</strong><small>当前内容不会自动被替换。</small></div><pre>${escapeHtml(suggestion)}</pre><button class="button button-quiet button-small" type="button" data-action="adopt-${scope}-prompt">采用建议（仍需保存）</button></section>`;
  }

  generatedLinesPanel(lines) {
    return `<section class="generated-lines-panel"><header><strong>台词草稿</strong><small>${lines.length} 句 · 尚未加入台本</small></header><ol>${lines.map((line) => `<li>${escapeHtml(line.text)}</li>`).join("")}</ol><button class="button button-quiet button-small" type="button" data-action="adopt-generated-lines">加入台本编辑器</button></section>`;
  }

  scriptLine(scriptId, item, index, canGenerate) {
    const sequence = Number(item.order || index + 1);
    return `<div class="script-line" data-script-item><div class="line-heading"><span class="line-number">${String(sequence).padStart(2, "0")}</span><div class="line-actions"><button class="button button-quiet button-small" type="button" data-action="generate-line" data-line-number="${sequence}"${canGenerate ? "" : " disabled"}>单条生成</button></div></div><div class="line-fields"><label><span>台词</span><textarea name="text" maxlength="2000">${escapeHtml(item.text)}</textarea></label><label><span>发音</span><textarea name="pronunciation" maxlength="2000">${escapeHtml(item.pronunciation)}</textarea></label></div>${this.lineResult(scriptId, sequence)}</div>`;
  }

  lineResult(scriptId, sequence) {
    const jobs = this.generationJobsForScript(scriptId);
    const records = [];
    for (const job of jobs) {
      const detail = this.state.jobDetails[job.id];
      const item = detail?.items?.find((entry) => Number(entry.sequence) === sequence);
      if (item) records.push(this.renderLineHistory(job, item));
      else if (!detail && Number(job.total_items) > 1) records.push(this.renderLineHistory(job, null));
    }
    if (!records.length) return "";
    return `<section class="line-history" aria-label="第 ${sequence} 行生成历史"><div class="line-history-heading"><span>生成历史</span><small>${records.length} 条</small></div>${records.join("")}</section>`;
  }

  renderLineHistory(job, item) {
    const status = item?.status || job.status;
    const audio = item?.audio_url || item?.candidates?.find((candidate) => candidate.accepted || candidate.audio_url)?.audio_url;
    const canDelete = Boolean(item?.id) && !["queued", "running"].includes(status) && !["queued", "running"].includes(job.status);
    const scope = Number(job.total_items) === 1 ? "单条" : "全部";
    const deleteButton = canDelete ? `<button class="button button-danger button-small" type="button" data-action="delete-generation" data-job-id="${escapeHtml(job.id)}" data-item-id="${escapeHtml(item.id)}">删除</button>` : "";
    const media = audio ? `<div class="line-history-media"><audio controls preload="none" src="${escapeHtml(audio)}"></audio><a class="button button-quiet button-small" href="${escapeHtml(item.download_url || audio)}" download>下载</a></div>` : `<small class="line-history-message">${escapeHtml(item?.error || (status === "completed" ? "音频详情暂不可用" : "音频生成后会显示在这里"))}</small>`;
    return `<article class="line-history-row"><div class="line-history-main"><div class="line-history-top"><span class="status-pill ${status === "failed" ? "danger" : status !== "completed" ? "warning" : ""}">${escapeHtml(statusLabel(status))}</span><span class="line-history-scope">${scope}</span><time>${escapeHtml(formatDate(job.submitted_at))}</time></div><div class="line-history-meta"><span>声音：${escapeHtml(job.voice_name || job.voice_id || "未指定")}</span><span>模型：${escapeHtml(this.modelLabel(job.model_id))}</span><span class="line-history-id">任务：${escapeHtml(job.id)}</span></div>${media}</div>${deleteButton}</article>`;
  }

  modelLabel(modelId) {
    const model = this.state.config.models.find((item) => item.id === modelId);
    return model?.label || modelId || "未指定";
  }

  generationJobsForScript(scriptId) {
    return this.state.jobs.filter((job) => job.script_id === scriptId);
  }

  renderVoice() {
    const focus = this.captureVoiceFocus();
    const selected = this.state.voices.find((item) => item.id === this.state.selectedVoiceId);
    const query = String(this.state.voiceSearchQuery || "").trim().toLowerCase();
    const voices = query ? this.state.voices.filter((voice) => String(voice.search_text || `${voice.name} ${voice.notes}`).toLowerCase().includes(query)) : this.state.voices;
    const list = voices.map((voice) => `<button class="asset-row${voice.id === selected?.id ? " active" : ""}" type="button" data-action="select-voice" data-id="${escapeHtml(voice.id)}"><span class="asset-icon">♪</span><span class="asset-copy"><strong>${escapeHtml(voice.name)}</strong><small>${voice.enabled_file_count}/${voice.file_count} 条录音</small></span><span class="asset-status ${voice.enabled_file_count ? "ready" : "pending"}">${voice.enabled_file_count ? "可用" : "待录入"}</span></button>`).join("") || (query && this.state.voices.length ? '<p class="empty-list">没有匹配的声音</p>' : '<p class="empty-list">尚未创建声音</p>');
    const detail = selected ? this.voiceDetail(selected) : '<div class="empty-panel"><div><strong>选择或创建一个声音</strong>声音库中的可用录音可以在台本页直接使用。</div></div>';
    byId("voiceContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">VOICE LIBRARY</span><h1>声音库</h1><p>维护项目里的参考录音，生成时从这里选择声音。</p></div><button class="button button-primary" type="button" data-action="open-voice">添加声音</button></div><div class="split-workspace"><aside class="asset-sidebar"><header class="asset-sidebar-header"><div><h2>声音</h2><small>${this.state.voices.length} 个声音</small></div></header><label class="asset-search" for="voiceSearch"><input id="voiceSearch" type="search" value="${escapeHtml(this.state.voiceSearchQuery)}" autocomplete="off" placeholder="搜索声音" aria-label="搜索声音"></label><div class="asset-list">${list}</div></aside><section class="detail-panel">${detail}</section></div>`;
    this.restoreVoiceFocus(focus);
    if (selected) void this.loadVoiceDetail(selected.id);
  }

  captureVoiceFocus() {
    const active = document.activeElement;
    if (active?.id !== "voiceSearch") return null;
    return { start: active.selectionStart, end: active.selectionEnd };
  }

  restoreVoiceFocus(focus) {
    if (!focus) return;
    const search = document.querySelector("#voiceSearch");
    if (!search) return;
    search.focus({ preventScroll: true });
    if (Number.isInteger(focus.start)) search.setSelectionRange(focus.start, Number.isInteger(focus.end) ? focus.end : focus.start);
  }

  async loadVoiceDetail(id) {
    if (this.state.voiceDetail?.id === id || this.loadingVoice === id) return;
    this.loadingVoice = id;
    try { const { voice } = await this.api.get(`/api/voices/${enc(id)}`); if (this.state.selectedVoiceId === id) { this.state.voiceDetail = voice; this.renderVoice(); } } catch (error) { this.toast(error.message, true); } finally { this.loadingVoice = null; }
  }

  voiceDetail(voice) {
    const detail = this.state.voiceDetail?.id === voice.id ? this.state.voiceDetail : null;
    if (!detail) return '<div class="empty-panel">正在读取声音...</div>';
    const emotions = this.state.config.reference_emotions || [];
    const files = detail.files.map((file) => `<div class="voice-file-row"><div class="voice-file-name"><strong>${escapeHtml(file.original_name)}</strong><small>${formatBytes(file.size_bytes)} · ${file.quality?.grade || "待检测"}</small></div><audio controls preload="none" src="${escapeHtml(file.audio_url)}"></audio><div class="voice-file-controls"><select data-action="file-emotion" data-file-id="${escapeHtml(file.id)}">${emotions.map((emotion) => `<option value="${escapeHtml(emotion.id)}"${emotion.id === file.emotion_tag ? " selected" : ""}>${escapeHtml(emotion.label)}</option>`).join("")}</select><label class="switch"><input type="checkbox" data-action="file-enabled" data-file-id="${escapeHtml(file.id)}"${file.enabled ? " checked" : ""}>启用</label></div></div>`).join("") || '<p class="empty-list">没有参考录音</p>';
    return `<div class="detail-inner"><header class="detail-header"><div><span class="eyebrow">VOICE DETAIL</span><h2>${escapeHtml(detail.name)}</h2><p>${detail.enabled_file_count}/${detail.file_count} 条启用录音 · ${formatBytes(detail.size_bytes)}</p></div></header><form id="voiceSettingsForm" class="detail-form"><label class="field"><span>声音名称</span><input name="name" value="${escapeHtml(detail.name)}" maxlength="80" required></label><label class="field wide"><span>说明</span><textarea name="notes" maxlength="500">${escapeHtml(detail.notes)}</textarea></label><div class="form-actions"><button class="button button-primary" type="submit">保存声音信息</button></div></form><div class="voice-detail-grid"><div><div class="editor-toolbar"><div><h3>参考录音</h3><p>启用的录音可作为生成参考。</p></div></div><div class="voice-files">${files}</div></div><form id="voiceAppendForm" class="voice-append"><label><span>追加参考录音</span><input name="files" type="file" accept="audio/*" multiple required></label><button class="button button-quiet" type="submit">追加录音</button></form></div></div>`;
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
    byId("projectContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">PROJECT SETTINGS</span><h1>项目资料与成员</h1><p>项目定义所有台本、声音与生成历史的共享边界。</p></div></div><div class="project-layout"><section class="project-section work-section"><header class="section-heading"><div><h2>项目资料</h2><p>更新名称、说明和所有台本共用的总体提示词</p></div></header>${canManage ? `<form id="projectSettingsForm" class="project-settings-form"><label class="field"><span>项目名称</span><input name="name" value="${escapeHtml(project.name)}" maxlength="80" required></label><label class="field"><span>项目说明</span><textarea name="description" maxlength="500">${escapeHtml(project.description)}</textarea></label><label class="field"><span>项目总体提示词</span><textarea name="prompt" maxlength="12000" placeholder="统一定义世界观、角色基调、语言风格、输出格式和禁用项。">${escapeHtml(this.state.projectPromptDraft ?? project.prompt ?? "")}</textarea><small>所有台本都会继承这段提示词；单台本提示词可进一步收窄。</small></label><div class="form-actions"><button class="button button-quiet" type="button" data-action="suggest-project-prompt">让 AI 完善提示词</button><button class="button button-primary" type="submit">保存资料</button></div>${this.promptSuggestion(this.state.projectPromptSuggestion, "project")}</form>` : `<div class="section-body"><p>${escapeHtml(project.description || "暂无项目说明")}</p><div class="readonly-prompt"><strong>项目总体提示词</strong><p>${escapeHtml(project.prompt || "尚未设置")}</p></div></div>`}</section><section class="project-section work-section"><header class="section-heading"><div><h2>成员</h2><p>${project.member_count} 位项目成员</p></div></header><div id="memberList" class="member-list"><p class="empty-list">正在读取成员...</p></div>${canManage ? '<form id="memberAddForm" class="member-add"><span>按用户名添加已由系统管理员创建的账户。</span><div class="inline-form"><input name="username" maxlength="64" required placeholder="member@example.com"><button class="button button-quiet" type="submit">添加成员</button></div></form>' : ""}<p class="project-id-note">PROJECT ID · ${escapeHtml(project.id)}</p></section></div>`;
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

  async click(event) {
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
      if (action === "view") this.showView(button.dataset.viewTarget);
      if (action === "open-project-dialog") this.openDialog("projectDialog");
      if (action === "open-script") this.openDialog("scriptDialog");
      if (action === "open-voice") this.openDialog("voiceDialog");
      if (action === "refresh") await this.selectProject(this.state.project?.id || "", true);
      if (action === "select-script") { this.state.selectedScriptId = button.dataset.id; this.state.scriptDetail = null; this.state.scriptPromptSuggestion = ""; this.state.scriptPromptDraft = null; this.state.generatedLines = []; this.state.generatedLinesScriptId = null; this.storePosition(); this.renderScript({ focusScriptId: button.dataset.id }); }
      if (action === "select-voice") { this.state.selectedVoiceId = button.dataset.id; this.state.voiceDetail = null; this.storePosition(); this.renderVoice(); this.renderScript(); }
      if (action === "generate-all") this.openGenerationDialog(null);
      if (action === "generate-line") this.openGenerationDialog(Number(button.dataset.lineNumber));
      if (action === "delete-generation") await this.deleteGeneration(button.dataset.jobId, button.dataset.itemId);
      if (action === "delete-all-generations") await this.deleteAllGenerations();
      if (action === "delete-script") await this.deleteScript();
      if (action === "remove-member") await this.removeMember(button.dataset.memberId);
      if (action === "suggest-project-prompt") await this.suggestProjectPrompt();
      if (action === "suggest-script-prompt") await this.suggestScriptPrompt();
      if (action === "adopt-project-prompt") this.adoptProjectPrompt();
      if (action === "adopt-script-prompt") this.adoptScriptPrompt();
      if (action === "adopt-generated-lines") this.adoptGeneratedLines();
    } catch (error) { this.toast(error.message, true); }
  }

  input(event) {
    const input = event.target;
    if (input.id === "scriptSearch") {
      const previousQuery = this.state.scriptSearchQuery;
      this.state.scriptSearchQuery = input.value;
      const cleared = !input.value.trim() && previousQuery.trim();
      this.renderScript(cleared ? { focusScriptId: this.state.selectedScriptId } : {});
    }
    if (input.id === "voiceSearch") {
      this.state.voiceSearchQuery = input.value;
      this.renderVoice();
    }
  }

  async change(event) {
    const input = event.target;
    try {
      if (input.dataset.action === "file-enabled") await this.updateVoiceFile(input.dataset.fileId, { enabled: input.checked });
      if (input.dataset.action === "file-emotion") await this.updateVoiceFile(input.dataset.fileId, { emotion_tag: input.value });
      if (input.dataset.action === "member-role") await this.updateMemberRole(input.dataset.memberId, input.value);
      if (input.dataset.action === "generation-voice-option" || input.dataset.action === "generation-model-option") {
        const selector = input.dataset.action === "generation-voice-option"
          ? 'input[data-action="generation-voice-option"]:checked'
          : 'input[data-action="generation-model-option"]:checked';
        const values = [...document.querySelectorAll(selector)].map((item) => item.value);
        if (values.length > 4) {
          input.checked = false;
          this.toast("每类最多选择 4 个", true);
          return;
        }
        if (input.dataset.action === "generation-voice-option") this.state.generationVoiceIds = values;
        else this.state.generationModelIds = values;
        this.storePosition();
        this.updateGenerationCombinationCount();
      }
    } catch (error) { this.toast(error.message, true); }
  }

  openGenerationDialog(lineNumber = null) {
    const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const voices = this.state.voices.filter((voice) => voice.enabled_file_count);
    const models = this.state.config.models.filter((model) => model.available === true);
    if (!script || !voices.length || !models.length) {
      this.toast("请先添加可用声音并配置可用模型", true);
      return;
    }
    const validVoiceIds = new Set(voices.map((voice) => voice.id));
    const validModelIds = new Set(models.map((model) => model.id));
    this.state.generationRequest = { lineNumber };
    this.state.generationVoiceIds = this.state.generationVoiceIds.filter((id) => validVoiceIds.has(id));
    this.state.generationModelIds = this.state.generationModelIds.filter((id) => validModelIds.has(id));
    if (!this.state.generationVoiceIds.length) this.state.generationVoiceIds = [this.state.selectedVoiceId].filter((id) => validVoiceIds.has(id));
    if (!this.state.generationModelIds.length) this.state.generationModelIds = [this.state.selectedModelId].filter((id) => validModelIds.has(id));
    if (!this.state.generationVoiceIds.length) this.state.generationVoiceIds = [voices[0].id];
    if (!this.state.generationModelIds.length) this.state.generationModelIds = [models[0].id];
    byId("generationOptionsTitle").textContent = lineNumber === null ? "全部生成" : `第 ${lineNumber} 行生成`;
    byId("generationVoiceChoices").innerHTML = voices.map((voice) => `<label class="generation-choice"><input type="checkbox" data-action="generation-voice-option" value="${escapeHtml(voice.id)}"${this.state.generationVoiceIds.includes(voice.id) ? " checked" : ""}><span><strong>${escapeHtml(voice.name)}</strong><small>${voice.enabled_file_count} 条可用录音</small></span></label>`).join("");
    byId("generationModelChoices").innerHTML = models.map((model) => `<label class="generation-choice"><input type="checkbox" data-action="generation-model-option" value="${escapeHtml(model.id)}"${this.state.generationModelIds.includes(model.id) ? " checked" : ""}><span><strong>${escapeHtml(model.label || model.id)}</strong><small>${escapeHtml(model.engine || model.id)}</small></span></label>`).join("");
    this.updateGenerationCombinationCount();
    this.openDialog("generationOptionsDialog");
  }

  updateGenerationCombinationCount() {
    const voices = this.state.generationVoiceIds.length;
    const models = this.state.generationModelIds.length;
    const count = voices * models;
    const lineNumber = this.state.generationRequest?.lineNumber;
    const target = lineNumber === null || lineNumber === undefined ? "整个台本" : `第 ${lineNumber} 行`;
    byId("generationCombinationCount").textContent = count ? `${target} · ${voices} 个声音 × ${models} 个模型 = ${count} 个任务` : "至少选择一个声音和一个模型";
  }

  async submit(event) {
    const form = event.target;
    if (!form.matches("form")) return;
    event.preventDefault();
    try {
      if (form.id === "projectCreateForm") await this.createProject(form);
      if (form.id === "scriptCreateForm") await this.createScript(form);
      if (form.id === "voiceCreateForm") await this.createVoice(form);
      if (form.id === "scriptSettingsForm") await this.saveScriptSettings(form);
      if (form.id === "scriptItemsForm") await this.saveScriptItems(form);
      if (form.id === "voiceSettingsForm") await this.saveVoiceSettings(form);
      if (form.id === "voiceAppendForm") await this.appendVoiceFiles(form);
      if (form.id === "projectSettingsForm") await this.saveProject(form);
      if (form.id === "memberAddForm") await this.addMember(form);
      if (form.id === "generationOptionsForm") await this.submitGenerationOptions(form);
      if (form.id === "textGenerationForm") await this.generateText(form);
    } catch (error) { this.toast(error.message, true); }
  }

  async createProject(form) { const { project } = await this.api.post("/api/projects", { name: form.name.value.trim(), description: form.description.value.trim() }); this.state.projects.push(project); form.reset(); byId("projectDialog").close(); await this.selectProject(project.id, true); this.showView("script"); this.toast("项目已创建"); }
  async createScript(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { script } = await this.api.postForm("/api/scripts", data); this.state.scripts.unshift(script); this.state.selectedScriptId = script.id; this.state.scriptDetail = null; form.reset(); byId("scriptUploadFileName").textContent = "选择台本文件"; byId("scriptDialog").close(); this.render(); this.showView("script"); this.toast("台本已导入"); }
  async createVoice(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { voice } = await this.api.postForm("/api/voices", data); this.state.voices.unshift(voice); this.state.selectedVoiceId = voice.id; this.state.voiceDetail = null; form.reset(); byId("voiceFileName").textContent = "选择参考录音"; byId("voiceDialog").close(); this.render(); this.showView("voice"); this.toast("声音已创建"); }
  async saveScriptSettings(form) { const id = this.state.selectedScriptId; const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, { name: form.name.value.trim(), prompt: form.prompt.value }); this.merge(this.state.scripts, script); this.state.scriptDetail = { ...this.state.scriptDetail, ...script }; this.state.scriptPromptSuggestion = ""; this.state.scriptPromptDraft = null; this.render(); this.toast("台本设置已保存"); }
  async saveScriptItems(form, options = {}) { const id = this.state.selectedScriptId; const items = [...form.querySelectorAll("[data-script-item]")].map((row) => ({ text: row.querySelector('[name="text"]').value, pronunciation: row.querySelector('[name="pronunciation"]').value })); const { script } = await this.api.put(`/api/scripts/${enc(id)}/items`, { items }); this.state.scriptDetail = script; this.merge(this.state.scripts, script); if (options.rerender !== false) this.renderScript(); if (options.notify !== false) this.toast("台词与发音已保存"); return script; }
  async saveVoiceSettings(form) { const id = this.state.selectedVoiceId; const { voice } = await this.api.patch(`/api/voices/${enc(id)}`, { name: form.name.value.trim(), notes: form.notes.value.trim() }); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.render(); this.toast("声音信息已保存"); }
  async appendVoiceFiles(form) { const { voice } = await this.api.postForm(`/api/voices/${enc(this.state.selectedVoiceId)}/files`, new FormData(form)); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); form.reset(); this.renderVoice(); this.toast("参考录音已追加"); }
  async updateVoiceFile(fileId, changes) { const { voice } = await this.api.patch(`/api/voices/${enc(this.state.selectedVoiceId)}/files/${enc(fileId)}`, changes); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.renderVoice(); this.toast("录音设置已更新"); }
  async submitGenerationOptions(form) {
    const lineNumber = this.state.generationRequest?.lineNumber ?? null;
    const voiceIds = [...this.state.generationVoiceIds];
    const modelIds = [...this.state.generationModelIds];
    if (!voiceIds.length || !modelIds.length) {
      this.toast("至少选择一个声音和一个模型", true);
      return;
    }
    await this.generateScript(lineNumber, voiceIds, modelIds);
    form.reset();
    byId("generationOptionsDialog").close();
    this.state.generationRequest = null;
  }

  async generateScript(lineNumber = null, voiceIds = [], modelIds = []) {
    const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const voices = this.state.voices.filter((item) => item.enabled_file_count);
    const models = this.state.config.models.filter((model) => model.available === true);
    voiceIds = voiceIds.filter((id) => voices.some((voice) => voice.id === id));
    modelIds = modelIds.filter((id) => models.some((model) => model.id === id));
    if (!script || !voiceIds.length || !modelIds.length) { this.toast("请至少选择一个可用声音和模型", true); return; }
    const form = byId("scriptItemsForm");
    if (form) await this.saveScriptItems(form, { notify: false, rerender: false });
    const data = new FormData();
    data.set("project_id", this.state.project.id);
    data.set("script_id", script.id);
    data.set("voice_id", voiceIds[0]);
    data.set("voice_ids", JSON.stringify(voiceIds));
    data.set("model_id", modelIds[0]);
    data.set("model_ids", JSON.stringify(modelIds));
    data.set("candidate_count", "1");
    data.set("reference_emotion", "all");
    data.set("generation_settings", "{}");
    data.set("name", lineNumber === null ? script.name : `${script.name} · 第 ${lineNumber} 行`);
    if (lineNumber !== null) data.set("line_number", String(lineNumber));
    const result = await this.api.postForm("/api/jobs", data);
    const jobs = result.jobs || [result.job];
    this.state.jobs.unshift(...jobs);
    for (const job of jobs) this.state.jobDetails[job.id] = null;
    this.renderScript();
    const activeJobs = this.state.jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    byId("queueSummary").textContent = `${activeJobs} 个处理中`;
    byId("workerDot").className = "online";
    this.toast(lineNumber === null ? `全部台词已加入生成队列（${jobs.length} 个组合）` : `第 ${lineNumber} 行已加入生成队列（${jobs.length} 个组合）`);
    for (const job of jobs) void this.loadJobDetail(job.id, true);
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
    for (const job of jobs) delete this.state.jobDetails[job.id];
    this.renderScript();
    this.toast(`${jobs.length} 个生成结果已删除`);
  }
  async saveProject(form) { const { project } = await this.api.patch(`/api/projects/${enc(this.state.project.id)}`, { name: form.name.value.trim(), description: form.description.value.trim(), prompt: form.prompt.value }); this.state.project = project; this.state.projectPromptSuggestion = ""; this.state.projectPromptDraft = null; this.merge(this.state.projects, project); this.render(); this.toast("项目资料已保存"); }
  async suggestProjectPrompt() { const form = byId("projectSettingsForm"); this.state.projectPromptDraft = form?.prompt?.value || ""; const { suggestion } = await this.api.post(`/api/projects/${enc(this.state.project.id)}/prompt-suggestion`, { goal: this.state.projectPromptDraft }); this.state.projectPromptSuggestion = suggestion; this.renderProject(); this.toast("已生成项目提示词建议"); }
  adoptProjectPrompt() { const form = byId("projectSettingsForm"); if (!form || !this.state.projectPromptSuggestion) return; form.prompt.value = this.state.projectPromptSuggestion; this.toast("建议已放入编辑框，请保存"); }
  async suggestScriptPrompt() { const form = byId("scriptSettingsForm"); this.state.scriptPromptDraft = form?.prompt?.value || ""; const { suggestion } = await this.api.post(`/api/scripts/${enc(this.state.selectedScriptId)}/prompt-suggestion`, { goal: this.state.scriptPromptDraft }); this.state.scriptPromptSuggestion = suggestion; this.renderScript(); this.toast("已生成台本提示词建议"); }
  adoptScriptPrompt() { const form = byId("scriptSettingsForm"); if (!form || !this.state.scriptPromptSuggestion) return; form.prompt.value = this.state.scriptPromptSuggestion; this.toast("建议已放入编辑框，请保存"); }
  async generateText(form) { const { lines } = await this.api.post(`/api/scripts/${enc(this.state.selectedScriptId)}/generate-text`, { instruction: form.instruction.value, line_count: Number(form.line_count.value) }); this.state.generatedLines = lines; this.state.generatedLinesScriptId = this.state.selectedScriptId; this.renderScript(); this.toast(`已生成 ${lines.length} 句台词草稿`); }
  adoptGeneratedLines() { const detail = this.state.scriptDetail; const lines = this.state.generatedLinesScriptId === detail?.id ? this.state.generatedLines : []; if (!detail || !lines.length) return; const items = [...detail.items]; for (const line of lines) items.push({ order: items.length + 1, source_line: items.length + 1, text: line.text, pronunciation: line.pronunciation, generated_text: line.text, direction: "flat", emphasis: [], hold_units: [], raw_mode: false }); this.state.scriptDetail = { ...detail, items, item_count: items.length }; this.state.generatedLines = []; this.state.generatedLinesScriptId = null; this.renderScript(); this.toast("草稿已加入编辑器，请保存行内容"); }
  async addMember(form) { await this.api.post(`/api/projects/${enc(this.state.project.id)}/members`, { username: form.username.value.trim() }); form.reset(); await this.refreshProjectAndMembers(); this.toast("成员已添加"); }
  async removeMember(memberId) { if (!window.confirm("确定移除该成员吗？")) return; await this.api.delete(`/api/projects/${enc(this.state.project.id)}/members/${enc(memberId)}`); await this.refreshProjectAndMembers(); this.toast("成员已移除"); }
  async updateMemberRole(memberId, role) { await this.api.patch(`/api/projects/${enc(this.state.project.id)}/members/${enc(memberId)}`, { role }); await this.loadMembers(this.state.project.id); this.toast("成员角色已更新"); }
  async refreshProjectAndMembers() { const { project } = await this.api.get(`/api/projects/${enc(this.state.project.id)}`); this.state.project = project; this.merge(this.state.projects, project); this.renderProject(); }
  async deleteScript() { const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId); if (!script || !window.confirm(`确定删除台本“${script.name}”吗？`)) return; await this.api.delete(`/api/scripts/${enc(script.id)}`); this.state.scripts = this.state.scripts.filter((item) => item.id !== script.id); this.state.selectedScriptId = this.state.scripts[0]?.id || null; this.state.scriptDetail = null; this.storePosition(); this.render(); this.toast("台本已删除"); }
  merge(items, value) { const index = items.findIndex((item) => item.id === value.id); if (index >= 0) items[index] = { ...items[index], ...value }; }
  openDialog(id) { byId(id).showModal(); }
  toast(message, error = false) { const element = byId("toast"); element.textContent = message; element.classList.toggle("error", error); element.classList.add("show"); clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => element.classList.remove("show"), 3000); }
}

new WorkstationApp().start();
