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

class WorkstationApp {
  constructor() {
    this.api = new ApiClient();
    this.state = { user: null, project: null, projects: [], scripts: [], voices: [], jobs: [], config: { models: [] }, selectedScriptId: null, selectedVoiceId: null, selectedJobId: null, scriptSearchQuery: "" };
    this.auth = new AuthController(this.api, "appShell", (user) => this.boot(user));
    this.requestVersion = 0;
    this.toastTimer = null;
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

  async selectProject(id, initial = false) {
    if (!id) { this.state.project = null; this.state.scripts = []; this.state.voices = []; this.state.jobs = []; this.render(); return; }
    const previousProjectId = this.state.project?.id || null;
    const version = ++this.requestVersion;
    try {
      const [projectResult, scriptsResult, voicesResult, jobsResult] = await Promise.all([
        this.api.get(`/api/projects/${enc(id)}`),
        this.api.get(`/api/scripts?project_id=${enc(id)}`),
        this.api.get(`/api/voices?project_id=${enc(id)}`),
        this.api.get(`/api/jobs?limit=100&project_id=${enc(id)}`),
      ]);
      if (version !== this.requestVersion) return;
      this.state.project = projectResult.project;
      this.state.scripts = scriptsResult.scripts;
      this.state.voices = voicesResult.voices;
      this.state.jobs = jobsResult.jobs;
      if (previousProjectId !== id) this.state.scriptSearchQuery = "";
      this.state.selectedScriptId = this.state.scripts.some((item) => item.id === this.state.selectedScriptId) ? this.state.selectedScriptId : this.state.scripts[0]?.id || null;
      this.state.selectedVoiceId = this.state.voices.some((item) => item.id === this.state.selectedVoiceId) ? this.state.selectedVoiceId : this.state.voices[0]?.id || null;
      this.state.selectedJobId = this.state.jobs.some((item) => item.id === this.state.selectedJobId) ? this.state.selectedJobId : this.state.jobs[0]?.id || null;
      this.storeProjectId(id);
      this.renderProjectSelect();
      this.render();
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
    byId("railProjectName").textContent = project?.name || "尚未选择项目";
    byId("railProjectRole").textContent = project ? roleLabel(project.project_role) : "--";
    byId("queueSummary").textContent = this.state.jobs.some((job) => ["queued", "running"].includes(job.status)) ? "处理中" : "空闲";
    byId("workerDot").className = this.state.jobs.some((job) => job.status === "running") ? "online" : "";
    if (!project) return;
    this.renderOverview();
    this.renderScript();
    this.renderVoice();
    this.renderGeneration();
    this.renderProject();
  }

  showView(view) {
    document.querySelectorAll("[data-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; });
    document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    byId("workspaceMain").scrollTop = 0;
  }

  renderOverview() {
    const project = this.state.project;
    const active = this.state.jobs.filter((job) => ["queued", "running"].includes(job.status)).length;
    const activity = this.state.jobs.slice(0, 5).map((job) => `<div class="activity-row"><i class="activity-dot ${escapeHtml(job.status)}"></i><div class="activity-main"><strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(job.script_name)} · ${escapeHtml(statusLabel(job.status))}</small></div><time class="activity-time">${formatDate(job.submitted_at)}</time></div>`).join("") || '<p class="empty-list">还没有生成记录。选择台本、声音和模型后可以创建首个生成任务。</p>';
    byId("overviewContent").innerHTML = `
      <div class="view-header"><div><span class="eyebrow">PROJECT OVERVIEW</span><h1>${escapeHtml(project.name)}</h1><p>${escapeHtml(project.description || "为项目补充目标和交付说明。")}</p></div><button class="button button-primary" type="button" data-action="view" data-view-target="generation">创建生成任务</button></div>
      <div class="metrics-grid"><div class="metric"><span>台本</span><strong>${this.state.scripts.length}</strong><small>可编辑台词</small></div><div class="metric"><span>声音</span><strong>${this.state.voices.length}</strong><small>共享参考音</small></div><div class="metric"><span>生成任务</span><strong>${this.state.jobs.length}</strong><small>${active ? `${active} 个处理中` : "队列空闲"}</small></div><div class="metric"><span>项目成员</span><strong>${project.member_count}</strong><small>${roleLabel(project.project_role)}</small></div></div>
      <div class="workflow-strip">${[["01", "台本", this.state.scripts.length, "script"], ["02", "声音", this.state.voices.length, "voice"], ["03", "任务", this.state.jobs.length, "generation"], ["04", "导出", this.state.jobs.filter((job) => job.status === "completed").length, "generation"]].map(([index, name, count, view]) => `<button class="workflow-stage${count ? " active" : ""}" type="button" data-action="view" data-view-target="${view}"><div class="workflow-stage-top"><span class="workflow-stage-index">${index}</span><span>${count ? "已就绪" : "待处理"}</span></div><strong>${name}</strong><b>${count}</b></button>`).join("")}</div>
      <div class="content-grid"><section class="work-section"><header class="section-heading"><div><h2>最近生成</h2><p>任务状态与候选采用记录</p></div><button class="button button-quiet button-small" type="button" data-action="refresh">刷新</button></header><div class="activity-list">${activity}</div></section><section class="work-section"><header class="section-heading"><div><h2>下一步</h2><p>保持资产和生成链路在同一项目中</p></div></header><div class="quick-links"><button class="quick-link" type="button" data-action="view" data-view-target="script"><span>导入或编辑台本</span><span>→</span></button><button class="quick-link" type="button" data-action="view" data-view-target="voice"><span>维护声音参考录音</span><span>→</span></button><button class="quick-link" type="button" data-action="view" data-view-target="project"><span>邀请项目成员</span><span>→</span></button></div></section></div>`;
  }

  renderScript(options = {}) {
    const focus = options.focusScriptId
      ? { type: "script", id: options.focusScriptId }
      : this.captureScriptFocus();
    const selected = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const query = String(this.state.scriptSearchQuery || "").trim().toLowerCase();
    const scripts = query ? this.state.scripts.filter((script) => String(script.name || "").toLowerCase().includes(query)) : this.state.scripts;
    const list = scripts.map((script) => `<button class="asset-row${script.id === selected?.id ? " active" : ""}" type="button" data-action="select-script" data-id="${escapeHtml(script.id)}" aria-current="${script.id === selected?.id}"><span class="asset-icon">T</span><span class="asset-copy"><strong>${escapeHtml(script.name)}</strong><small>${script.item_count} 行 · ${formatDate(script.created_at)}</small></span><span class="asset-status">台词</span></button>`).join("") || (query && this.state.scripts.length ? '<p class="empty-list">没有匹配的台本</p>' : '<p class="empty-list">尚未导入台本</p>');
    const detail = selected ? this.scriptDetail(selected) : '<div class="empty-panel"><div><strong>选择或导入一个台本</strong>上传后可编辑每行台词和发音。</div></div>';
    byId("scriptContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">SCRIPT LIBRARY</span><h1>台本与发言</h1><p>台本只保存原始台词、发音和发言顺序；声音在生成时选择。</p></div><button class="button button-primary" type="button" data-action="open-script">导入台本</button></div><div class="split-workspace"><aside class="asset-sidebar"><header class="asset-sidebar-header"><div><h2>台本</h2><small>${this.state.scripts.length} 个资产</small></div></header><label class="asset-search" for="scriptSearch"><input id="scriptSearch" type="search" value="${escapeHtml(this.state.scriptSearchQuery)}" autocomplete="off" placeholder="搜索台本" aria-label="搜索台本"></label><div class="asset-list">${list}</div></aside><section class="detail-panel">${detail}</section></div>`;
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
    return `<div class="detail-inner"><header class="detail-header"><div><span class="eyebrow">SCRIPT DETAIL</span><h2>${escapeHtml(detail.name)}</h2><p>${detail.item_count} 行台词 · ${escapeHtml(detail.original_name)}</p></div><div class="detail-actions"><a class="button button-quiet button-small" href="/api/scripts/${enc(detail.id)}/export">导出 CSV</a><button class="button button-danger button-small" type="button" data-action="delete-script">删除</button></div></header><form id="scriptSettingsForm" class="detail-form"><label class="field wide"><span>台本名称</span><input name="name" value="${escapeHtml(detail.name)}" maxlength="80" required></label><div class="form-actions"><button class="button button-primary" type="submit">保存台本名称</button></div></form><form id="scriptItemsForm" class="script-editor"><div class="editor-toolbar"><div><h3>台词与发音</h3><p>每一行都会作为独立生成单元保存。</p></div><button class="button button-primary button-small" type="submit">保存行内容</button></div><div class="script-lines">${detail.items.map((item, index) => `<div class="script-line" data-script-item><span class="line-number">${String(index + 1).padStart(2, "0")}</span><div class="line-fields"><label><span>台词</span><textarea name="text" maxlength="2000">${escapeHtml(item.text)}</textarea></label><label><span>发音</span><textarea name="pronunciation" maxlength="2000">${escapeHtml(item.pronunciation)}</textarea></label></div></div>`).join("")}</div></form></div>`;
  }

  renderVoice() {
    const selected = this.state.voices.find((item) => item.id === this.state.selectedVoiceId);
    const list = this.state.voices.map((voice) => `<button class="asset-row${voice.id === selected?.id ? " active" : ""}" type="button" data-action="select-voice" data-id="${escapeHtml(voice.id)}"><span class="asset-icon">♪</span><span class="asset-copy"><strong>${escapeHtml(voice.name)}</strong><small>${voice.enabled_file_count}/${voice.file_count} 条录音</small></span><span class="asset-status ${voice.enabled_file_count ? "ready" : "pending"}">${voice.enabled_file_count ? "可用" : "待录入"}</span></button>`).join("") || '<p class="empty-list">尚未创建声音</p>';
    const detail = selected ? this.voiceDetail(selected) : '<div class="empty-panel"><div><strong>选择或创建一个声音</strong>生成任务中可以选择一个或多个声音进行对比。</div></div>';
    byId("voiceContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">VOICE LIBRARY</span><h1>声音与参考录音</h1><p>声音属于当前项目，生成时可以一次选择多个声音对比。</p></div><button class="button button-primary" type="button" data-action="open-voice">创建声音</button></div><div class="split-workspace"><aside class="asset-sidebar"><header class="asset-sidebar-header"><div><h2>声音</h2><small>${this.state.voices.length} 个资产</small></div></header><div class="asset-list">${list}</div></aside><section class="detail-panel">${detail}</section></div>`;
    if (selected) void this.loadVoiceDetail(selected.id);
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

  renderGeneration() {
    const selected = this.state.jobs.find((item) => item.id === this.state.selectedJobId);
    const models = this.state.config.models.filter((model) => model.available === true);
    const voices = this.state.voices.filter((voice) => voice.enabled_file_count);
    const voiceChoices = voices.map((voice, index) => `<label class="choice-row"><input type="checkbox" name="voice_ids" value="${escapeHtml(voice.id)}"${index === 0 ? " checked" : ""}><span><strong>${escapeHtml(voice.name)}</strong><small>${voice.enabled_file_count} 条启用录音</small></span></label>`).join("") || '<p class="empty-list">尚无可用声音</p>';
    const modelChoices = models.map((model, index) => `<label class="choice-row"><input type="checkbox" name="model_ids" value="${escapeHtml(model.id)}"${index === 0 ? " checked" : ""}><span><strong>${escapeHtml(model.label || model.id)}</strong><small>${escapeHtml(model.id)}</small></span></label>`).join("");
    const form = this.state.scripts.length && voices.length && models.length ? `<form id="generationForm" class="generation-form work-section"><h3>创建生成任务</h3><label class="field"><span>台本</span><select name="script_id" required>${this.state.scripts.map((script) => `<option value="${escapeHtml(script.id)}">${escapeHtml(script.name)}</option>`).join("")}</select><small>台本只提供台词与发言，声音在这里选择。</small></label><fieldset class="choice-group"><legend>声音（可多选）</legend>${voiceChoices}</fieldset><fieldset class="choice-group"><legend>模型（可多选）</legend>${modelChoices}</fieldset><div class="two-columns"><label class="field"><span>候选数量</span><select name="candidate_count"><option value="2">2</option><option value="3">3</option></select></label><label class="field"><span>名称</span><input name="name" maxlength="80" placeholder="本次生成"></label></div><button class="button button-primary" type="submit">加入生成队列</button><p class="generation-note">每个声音与模型组合会生成独立任务，并共享候选种子，方便试听对比。</p></form>` : '<section class="generation-form work-section"><h3>创建生成任务</h3><p class="generation-note">需要至少一个台本、一个有启用录音的声音和一个可用模型。</p></section>';
    const history = this.state.jobs.map((job) => `<button class="history-row${job.id === selected?.id ? " active" : ""}" type="button" data-action="select-job" data-id="${escapeHtml(job.id)}"><span><strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(job.script_name)} · ${escapeHtml(job.voice_name)} · ${escapeHtml(job.model_id)} · ${formatDate(job.submitted_at)}</small></span><span class="history-state ${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</span></button>`).join("") || '<p class="empty-list">还没有生成任务</p>';
    byId("generationContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">GENERATION REVIEW</span><h1>生成与审核</h1><p>每次提交保留台本、声音和模型快照；选择候选后再导出正式音频。</p></div><button class="button button-quiet" type="button" data-action="refresh">刷新状态</button></div><div class="generation-layout">${form}<section class="generation-history work-section"><header class="section-heading"><div><h2>生成历史</h2><p>${this.state.jobs.length} 个任务</p></div></header><div class="history-list">${history}</div></section></div><section class="generation-detail work-section">${selected ? this.jobDetail(selected) : '<div class="empty-panel">选择一个生成任务查看候选与导出。</div>'}</section>`;
    if (selected) void this.loadJobDetail(selected.id);
  }

  async loadJobDetail(id) {
    if (this.state.jobDetail?.id === id || this.loadingJob === id) return;
    this.loadingJob = id;
    try { const { job } = await this.api.get(`/api/jobs/${enc(id)}`); if (this.state.selectedJobId === id) { this.state.jobDetail = job; this.renderGeneration(); } } catch (error) { this.toast(error.message, true); } finally { this.loadingJob = null; }
  }

  jobDetail(job) {
    const detail = this.state.jobDetail?.id === job.id ? this.state.jobDetail : null;
    if (!detail) return '<div class="empty-panel">正在读取任务...</div>';
    const items = detail.items.map((item) => `<div class="result-line"><div class="result-line-heading"><span>${String(item.sequence).padStart(2, "0")}</span><strong>${escapeHtml(item.text)}</strong><small>${escapeHtml(statusLabel(item.status))}</small></div><div class="candidate-grid">${item.candidates.map((candidate) => `<article class="candidate-card${candidate.accepted ? " accepted" : ""}"><div class="candidate-top"><strong>${escapeHtml(candidate.name || `候选 ${candidate.ordinal}`)}</strong><span>${candidate.accepted ? "已采用" : "待审核"}</span></div>${candidate.audio_url ? `<audio controls preload="none" src="${escapeHtml(candidate.audio_url)}"></audio>` : '<span class="candidate-pending">等待音频生成</span>'}${candidate.audio_url && !candidate.accepted ? `<button class="button button-quiet button-small" type="button" data-action="accept-candidate" data-item-id="${escapeHtml(item.id)}" data-candidate-id="${escapeHtml(candidate.id)}">采用此版本</button>` : ""}</article>`).join("") || '<span class="candidate-pending">暂无候选</span>'}</div></div>`).join("");
    return `<div class="section-body"><header class="generation-detail-header"><div><h3>${escapeHtml(detail.name)}</h3><p>${escapeHtml(detail.voice_name)} · ${escapeHtml(detail.model_id)} · ${detail.completed_items}/${detail.total_items} 段完成</p></div><div class="job-actions">${detail.status === "completed" ? `<a class="button button-quiet button-small" href="${escapeHtml(detail.download_url)}">下载全部</a>` : ""}${detail.can_export ? `<a class="button button-primary button-small" href="${escapeHtml(detail.export_url)}">正式导出</a>` : ""}</div></header><div class="generation-progress"><span style="width:${Math.max(0, Math.min(100, Number(detail.progress || 0)))}%"></span></div><div class="result-list">${items}</div></div>`;
  }

  renderProject() {
    const project = this.state.project;
    const canManage = project.can_manage;
    byId("projectContent").innerHTML = `<div class="view-header"><div><span class="eyebrow">PROJECT SETTINGS</span><h1>项目资料与成员</h1><p>项目定义所有台本、声音与生成历史的共享边界。</p></div></div><div class="project-layout"><section class="project-section work-section"><header class="section-heading"><div><h2>项目资料</h2><p>更新名称和项目说明</p></div></header>${canManage ? `<form id="projectSettingsForm" class="project-settings-form"><label class="field"><span>项目名称</span><input name="name" value="${escapeHtml(project.name)}" maxlength="80" required></label><label class="field"><span>项目说明</span><textarea name="description" maxlength="500">${escapeHtml(project.description)}</textarea></label><div class="form-actions"><button class="button button-primary" type="submit">保存资料</button></div></form>` : `<div class="section-body"><p>${escapeHtml(project.description || "暂无项目说明")}</p></div>`}</section><section class="project-section work-section"><header class="section-heading"><div><h2>成员</h2><p>${project.member_count} 位项目成员</p></div></header><div id="memberList" class="member-list"><p class="empty-list">正在读取成员...</p></div>${canManage ? '<form id="memberAddForm" class="member-add"><span>按用户名添加已由系统管理员创建的账户。</span><div class="inline-form"><input name="username" maxlength="64" required placeholder="member@example.com"><button class="button button-quiet" type="submit">添加成员</button></div></form>' : ""}<p class="project-id-note">PROJECT ID · ${escapeHtml(project.id)}</p></section></div>`;
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
      const { jobs } = await this.api.get(`/api/jobs?limit=100&project_id=${enc(projectId)}`);
      if (this.state.project?.id !== projectId) return;
      this.state.jobs = jobs;
      this.state.selectedJobId = jobs.some((job) => job.id === this.state.selectedJobId) ? this.state.selectedJobId : jobs[0]?.id || null;
      this.state.jobDetail = null;
      this.renderOverview();
      this.renderGeneration();
      byId("queueSummary").textContent = jobs.some((job) => ["queued", "running"].includes(job.status)) ? "处理中" : "空闲";
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
      if (action === "select-script") { this.state.selectedScriptId = button.dataset.id; this.state.scriptDetail = null; this.renderScript({ focusScriptId: button.dataset.id }); }
      if (action === "select-voice") { this.state.selectedVoiceId = button.dataset.id; this.state.voiceDetail = null; this.renderVoice(); }
      if (action === "select-job") { this.state.selectedJobId = button.dataset.id; this.state.jobDetail = null; this.renderGeneration(); }
      if (action === "delete-script") await this.deleteScript();
      if (action === "accept-candidate") await this.acceptCandidate(button.dataset.itemId, button.dataset.candidateId);
      if (action === "remove-member") await this.removeMember(button.dataset.memberId);
    } catch (error) { this.toast(error.message, true); }
  }

  input(event) {
    const input = event.target;
    if (input.id !== "scriptSearch") return;
    const previousQuery = this.state.scriptSearchQuery;
    this.state.scriptSearchQuery = input.value;
    const cleared = !input.value.trim() && previousQuery.trim();
    this.renderScript(cleared ? { focusScriptId: this.state.selectedScriptId } : {});
  }

  async change(event) {
    const input = event.target;
    try {
      if (input.dataset.action === "file-enabled") await this.updateVoiceFile(input.dataset.fileId, { enabled: input.checked });
      if (input.dataset.action === "file-emotion") await this.updateVoiceFile(input.dataset.fileId, { emotion_tag: input.value });
      if (input.dataset.action === "member-role") await this.updateMemberRole(input.dataset.memberId, input.value);
    } catch (error) { this.toast(error.message, true); }
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
      if (form.id === "generationForm") await this.createJob(form);
      if (form.id === "projectSettingsForm") await this.saveProject(form);
      if (form.id === "memberAddForm") await this.addMember(form);
    } catch (error) { this.toast(error.message, true); }
  }

  async createProject(form) { const { project } = await this.api.post("/api/projects", { name: form.name.value.trim(), description: form.description.value.trim() }); this.state.projects.push(project); form.reset(); byId("projectDialog").close(); await this.selectProject(project.id, true); this.showView("overview"); this.toast("项目已创建"); }
  async createScript(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { script } = await this.api.postForm("/api/scripts", data); this.state.scripts.unshift(script); this.state.selectedScriptId = script.id; this.state.scriptDetail = null; form.reset(); byId("scriptUploadFileName").textContent = "选择台本文件"; byId("scriptDialog").close(); this.render(); this.showView("script"); this.toast("台本已导入"); }
  async createVoice(form) { const data = new FormData(form); data.set("project_id", this.state.project.id); const { voice } = await this.api.postForm("/api/voices", data); this.state.voices.unshift(voice); this.state.selectedVoiceId = voice.id; this.state.voiceDetail = null; form.reset(); byId("voiceFileName").textContent = "选择参考录音"; byId("voiceDialog").close(); this.render(); this.showView("voice"); this.toast("声音已创建"); }
  async saveScriptSettings(form) { const id = this.state.selectedScriptId; const { script } = await this.api.patch(`/api/scripts/${enc(id)}`, { name: form.name.value.trim() }); this.merge(this.state.scripts, script); this.state.scriptDetail = { ...this.state.scriptDetail, ...script }; this.render(); this.toast("台本名称已保存"); }
  async saveScriptItems(form) { const id = this.state.selectedScriptId; const items = [...form.querySelectorAll("[data-script-item]")].map((row) => ({ text: row.querySelector('[name="text"]').value, pronunciation: row.querySelector('[name="pronunciation"]').value })); const { script } = await this.api.put(`/api/scripts/${enc(id)}/items`, { items }); this.state.scriptDetail = script; this.merge(this.state.scripts, script); this.renderScript(); this.toast("台词与发音已保存"); }
  async saveVoiceSettings(form) { const id = this.state.selectedVoiceId; const { voice } = await this.api.patch(`/api/voices/${enc(id)}`, { name: form.name.value.trim(), notes: form.notes.value.trim() }); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.render(); this.toast("声音信息已保存"); }
  async appendVoiceFiles(form) { const { voice } = await this.api.postForm(`/api/voices/${enc(this.state.selectedVoiceId)}/files`, new FormData(form)); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); form.reset(); this.renderVoice(); this.toast("参考录音已追加"); }
  async updateVoiceFile(fileId, changes) { const { voice } = await this.api.patch(`/api/voices/${enc(this.state.selectedVoiceId)}/files/${enc(fileId)}`, changes); this.state.voiceDetail = voice; this.merge(this.state.voices, voice); this.renderVoice(); this.toast("录音设置已更新"); }
  async createJob(form) { const data = new FormData(form); const voiceIds = data.getAll("voice_ids"); const modelIds = data.getAll("model_ids"); if (!voiceIds.length || !modelIds.length) { this.toast("请至少选择一个声音和一个模型", true); return; } data.delete("voice_ids"); data.set("voice_ids", JSON.stringify(voiceIds)); data.delete("model_ids"); data.set("model_id", modelIds[0]); data.set("model_ids", JSON.stringify(modelIds.slice(1))); data.set("project_id", this.state.project.id); data.set("generation_settings", "{}"); const result = await this.api.postForm("/api/jobs", data); this.state.jobs.unshift(...(result.jobs || [result.job])); this.state.selectedJobId = result.job.id; this.state.jobDetail = null; form.reset(); this.render(); this.toast(`${result.jobs?.length || 1} 个任务已加入生成队列`); }
  async acceptCandidate(itemId, candidateId) { await this.api.post(`/api/jobs/${enc(this.state.selectedJobId)}/items/${enc(itemId)}/accept`, { candidate_id: candidateId }); this.state.jobDetail = null; await this.loadJobDetail(this.state.selectedJobId); this.toast("候选已采用"); }
  async saveProject(form) { const { project } = await this.api.patch(`/api/projects/${enc(this.state.project.id)}`, { name: form.name.value.trim(), description: form.description.value.trim() }); this.state.project = project; this.merge(this.state.projects, project); this.render(); this.toast("项目资料已保存"); }
  async addMember(form) { await this.api.post(`/api/projects/${enc(this.state.project.id)}/members`, { username: form.username.value.trim() }); form.reset(); await this.refreshProjectAndMembers(); this.toast("成员已添加"); }
  async removeMember(memberId) { if (!window.confirm("确定移除该成员吗？")) return; await this.api.delete(`/api/projects/${enc(this.state.project.id)}/members/${enc(memberId)}`); await this.refreshProjectAndMembers(); this.toast("成员已移除"); }
  async updateMemberRole(memberId, role) { await this.api.patch(`/api/projects/${enc(this.state.project.id)}/members/${enc(memberId)}`, { role }); await this.loadMembers(this.state.project.id); this.toast("成员角色已更新"); }
  async refreshProjectAndMembers() { const { project } = await this.api.get(`/api/projects/${enc(this.state.project.id)}`); this.state.project = project; this.merge(this.state.projects, project); this.renderProject(); }
  async deleteScript() { const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId); if (!script || !window.confirm(`确定删除台本“${script.name}”吗？`)) return; await this.api.delete(`/api/scripts/${enc(script.id)}`); this.state.scripts = this.state.scripts.filter((item) => item.id !== script.id); this.state.selectedScriptId = this.state.scripts[0]?.id || null; this.state.scriptDetail = null; this.render(); this.toast("台本已删除"); }
  merge(items, value) { const index = items.findIndex((item) => item.id === value.id); if (index >= 0) items[index] = { ...items[index], ...value }; }
  openDialog(id) { byId(id).showModal(); }
  toast(message, error = false) { const element = byId("toast"); element.textContent = message; element.classList.toggle("error", error); element.classList.add("show"); clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => element.classList.remove("show"), 3000); }
}

new WorkstationApp().start();
