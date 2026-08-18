import { AuthController } from "./auth/auth-controller.js?v=20260815";
import { ApiClient } from "./core/api-client.js";
import { $, escapeHtml, setButtonBusy } from "./core/dom.js";
import { ProviderController } from "./providers/provider-controller.js";

const TITLES = {
  overview: ["SYSTEM OVERVIEW", "系统概览"],
  users: ["IDENTITIES", "账户管理"],
  projects: ["WORKSPACES", "全部项目"],
  jobs: ["GENERATION QUEUE", "生成任务"],
  assets: ["ASSET REGISTRY", "资产数据"],
  logs: ["AUDIT TRAIL", "审计日志"],
  providers: ["MODEL SERVICES", "模型与三方服务"],
};

class AdminShell {
  #timer;

  toast(message, error = false) {
    const element = $("#toast");
    element.textContent = message;
    element.classList.toggle("error", error);
    element.classList.add("show");
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => element.classList.remove("show"), 3200);
  }
}

class AdminApp {
  #api = new ApiClient();
  #shell = new AdminShell();
  #loadPromise = null;
  #assetLoadPromise = null;
  #state = {
    isAdmin: false,
    config: { models: [] },
    users: [],
    projects: [],
    jobs: [],
    logs: [],
    assets: { voices: [], scripts: [] },
    assetsLoaded: false,
    userFilter: { search: "", status: "all" },
    projectFilter: { search: "" },
    jobFilter: { search: "", status: "all", projectId: "" },
    assetFilter: { search: "", type: "all", projectId: "all" },
    logFilter: { search: "", result: "all" },
  };
  #auth = new AuthController(this.#api, "adminShell", (user) => this.#boot(user));
  #providers = new ProviderController({ state: this.#state, api: this.#api, shell: this.#shell });
  #resetUserId = null;

  async start() {
    this.#auth.bind();
    this.#bind();
    const user = await this.#auth.restore();
    if (user) this.#boot(user);
    requestAnimationFrame(() => document.documentElement.classList.remove("app-loading"));
  }

  #bind() {
    document.querySelectorAll("[data-admin-view]").forEach((button) => {
      button.addEventListener("click", () => this.#showView(button.dataset.adminView));
    });
    document.querySelectorAll("[data-jump-view]").forEach((button) => {
      button.addEventListener("click", () => this.#showView(button.dataset.jumpView));
    });
    document.querySelectorAll("[data-admin-close]").forEach((button) => {
      button.addEventListener("click", () => $(`#${button.dataset.adminClose}`).close());
    });
    $("#adminRefresh").addEventListener("click", () => this.#loadAll(true));
    $("#openUserDialog").addEventListener("click", () => $("#userDialog").showModal());
    $("#userCreateForm").addEventListener("submit", (event) => this.#createUser(event));
    $("#resetPasswordForm").addEventListener("submit", (event) => this.#resetPassword(event));
    $("#adminUserTable").addEventListener("click", (event) => this.#userAction(event));
    $("#adminUserSearch").addEventListener("input", (event) => {
      this.#state.userFilter.search = event.currentTarget.value;
      this.#renderUsers(this.#state.users);
    });
    $("#adminUserStatus").addEventListener("change", (event) => {
      this.#state.userFilter.status = event.currentTarget.value;
      this.#renderUsers(this.#state.users);
    });
    $("#adminProjectSearch").addEventListener("input", (event) => {
      this.#state.projectFilter.search = event.currentTarget.value;
      this.#renderProjects(this.#state.projects);
    });
    $("#adminJobSearch").addEventListener("input", (event) => {
      this.#state.jobFilter.search = event.currentTarget.value;
      this.#state.jobFilter.projectId = "";
      this.#renderJobs();
    });
    $("#adminJobStatus").addEventListener("change", (event) => {
      this.#state.jobFilter.status = event.currentTarget.value;
      this.#state.jobFilter.projectId = "";
      this.#renderJobs();
    });
    $("#adminJobTable").addEventListener("click", (event) => this.#jobAction(event));
    $("#adminProjectTable").addEventListener("click", (event) => this.#projectAction(event));
    $("#adminJobDetail").addEventListener("click", (event) => this.#jobAction(event));
    $("#adminAssetSearch").addEventListener("input", (event) => {
      this.#state.assetFilter.search = event.currentTarget.value;
      this.#renderAssets();
    });
    $("#adminAssetType").addEventListener("change", (event) => {
      this.#state.assetFilter.type = event.currentTarget.value;
      this.#renderAssets();
    });
    $("#adminAssetProject").addEventListener("change", (event) => {
      this.#state.assetFilter.projectId = event.currentTarget.value;
      this.#renderAssets();
    });
    $("#adminLogSearch").addEventListener("input", (event) => {
      this.#state.logFilter.search = event.currentTarget.value;
      this.#renderLogs(this.#state.logs);
    });
    $("#adminLogResult").addEventListener("change", (event) => {
      this.#state.logFilter.result = event.currentTarget.value;
      this.#renderLogs(this.#state.logs);
    });
    $("#adminProjectDetail").addEventListener("click", (event) => this.#projectAction(event));
    this.#providers.bind();
  }

  #boot(user) {
    this.#state.isAdmin = user.role === "system_admin";
    $("#adminName").textContent = user.display_name;
    $("#adminUsername").textContent = user.username;
    $("#adminAvatar").textContent = user.display_name.slice(0, 1);
    if (!this.#state.isAdmin) {
      $("#adminDenied").hidden = false;
      document.querySelectorAll(".admin-view").forEach((view) => {
        view.hidden = true;
      });
      return;
    }
    this.#showView("overview");
    void this.#loadAll();
  }

  #loadAll(force = false) {
    if (this.#loadPromise) {
      return force ? this.#loadPromise.then(() => this.#loadAll()) : this.#loadPromise;
    }
    this.#loadPromise = this.#loadAllOnce().finally(() => {
      this.#loadPromise = null;
    });
    return this.#loadPromise;
  }

  async #loadAllOnce() {
    const overviewPromise = this.#api
      .get("/api/admin/overview")
      .then((overview) => {
        this.#renderOverview(overview);
        return overview;
      })
      .catch((error) => {
        this.#shell.toast(error.message, true);
        return null;
      });
    try {
      const [users, projects, jobs, logs, config] = await Promise.all([
        this.#api.get("/api/admin/users"),
        this.#api.get("/api/admin/projects"),
        this.#api.get("/api/admin/jobs?limit=100"),
        this.#api.get("/api/admin/audit-logs?limit=300"),
        this.#api.get("/api/config"),
      ]);
      this.#state.config = config;
      this.#state.users = users.users;
      this.#state.projects = projects.projects;
      this.#state.jobs = jobs.jobs;
      this.#state.logs = logs.logs;
      this.#renderUsers(this.#state.users);
      this.#renderProjects(this.#state.projects);
      this.#renderJobs();
      this.#renderLogs(this.#state.logs);
      this.#renderAssetProjectOptions();
      if (this.#state.assetsLoaded) void this.#loadAssets();
      this.#providers.renderLocalModels();
      await this.#providers.ensureLoaded();
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
    await overviewPromise;
  }

  #showView(name) {
    if (!this.#state.isAdmin) return;
    const [kicker, title] = TITLES[name] || TITLES.overview;
    $("#adminViewKicker").textContent = kicker;
    $("#adminViewTitle").textContent = title;
    document.querySelectorAll("[data-admin-view]").forEach((button) => {
      button.classList.toggle("active", button.dataset.adminView === name);
    });
    document.querySelectorAll("[data-admin-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.adminPanel !== name;
      panel.classList.toggle("active", panel.dataset.adminPanel === name);
    });
    if (name === "assets" && this.#state.isAdmin && !this.#state.assetsLoaded) {
      void this.#loadAssets();
    }
  }

  #loadAssets() {
    if (this.#assetLoadPromise) return this.#assetLoadPromise;
    this.#assetLoadPromise = this.#loadAssetsOnce().finally(() => {
      this.#assetLoadPromise = null;
    });
    return this.#assetLoadPromise;
  }

  async #loadAssetsOnce() {
    $("#adminAssetTable").innerHTML = '<p class="muted-empty">正在读取资产数据…</p>';
    try {
      this.#state.assets = await this.#api.get("/api/admin/assets?limit=500");
      this.#state.assetsLoaded = true;
      this.#renderAssets();
    } catch (error) {
      $("#adminAssetTable").innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
      this.#shell.toast(error.message, true);
    }
  }

  #renderOverview(payload) {
    const counts = payload.counts;
    $("#metricUsers").textContent = counts.users;
    $("#metricActiveUsers").textContent = `${counts.active_users} 个启用`;
    $("#metricProjects").textContent = counts.projects;
    $("#metricMemberships").textContent = `${counts.memberships} 条成员关系`;
    $("#metricJobs").textContent = counts.jobs;
    $("#metricQueue").textContent = `${Number(counts.running || 0) + Number(counts.queued || 0)} 个处理中`;
    $("#metricVoices").textContent = counts.voices ?? "—";
    $("#metricScripts").textContent = counts.scripts ?? "—";
    $("#metricCompleted").textContent = counts.completed ?? "—";
    $("#metricFailed").textContent = counts.failed ?? "—";
    $("#metricStorage").textContent = formatBytes(payload.storage.data_bytes);
    $("#metricDisk").textContent = `${formatBytes(payload.storage.disk_free_bytes)} 可用`;
    const active = Number(payload.queue.running || 0) + Number(payload.queue.queued || 0);
    $("#adminWorkerDot").classList.toggle("online", Boolean(payload.queue.worker_alive));
    $("#adminQueueState").textContent = active ? `${active} 个任务处理中` : "队列空闲";
    $("#adminRecentJobs").innerHTML = table(
      ["任务", "项目", "模型", "状态", "提交时间"],
      payload.recent_jobs.map((job) => [
        `<strong>${escapeHtml(job.name)}</strong><small>${job.total_items} 段</small>`,
        escapeHtml(job.project_id || "—"),
        escapeHtml(job.model_id),
        `<span class="admin-status ${escapeHtml(job.status)}">${statusLabel(job.status)}</span>`,
        formatDate(job.submitted_at),
      ]),
    );
    const models = Object.values(payload.engine.models || {});
    $("#adminRuntime").innerHTML = models.length
      ? models.map((model) => `<div class="runtime-row"><span class="runtime-dot ${model.available ? "ok" : "off"}"></span><div><strong>${escapeHtml(model.label || model.id || "模型")}</strong><small>${model.available ? "可用" : escapeHtml(model.reason || "未安装")}</small></div><em>${model.loaded ? "已加载" : "待机"}</em></div>`).join("")
      : '<p class="muted-empty">暂无模型状态</p>';
    $("#adminAuditPreview").innerHTML = payload.recent_audit.slice(0, 6).map(auditRow).join("") || '<p class="muted-empty">暂无操作记录</p>';
    this.#renderInsights(payload.insights || {}, payload.queue || {});
  }

  #renderInsights(insights, queue) {
    const activity = insights.activity || [];
    const maxTotal = Math.max(1, ...activity.map((item) => Number(item.total || 0)));
    $("#adminActivityChart").innerHTML = activity.length
      ? activity.map((item) => {
        const total = Number(item.total || 0);
        const completed = Number(item.completed || 0);
        const failed = Number(item.failed || 0);
        const height = Math.max(6, Math.round((total / maxTotal) * 100));
        return `<div class="activity-column" title="${escapeHtml(item.day)} · ${total} 个任务"><div class="activity-bar" style="--bar-height:${height}%"><i style="--failed-height:${total ? Math.round((failed / total) * 100) : 0}%"></i></div><strong>${total}</strong><small>${escapeHtml(item.day.slice(5))}</small><em>${completed} 成功 · ${failed} 失败</em></div>`;
      }).join("")
      : '<p class="muted-empty">近期开启后会显示趋势</p>';
    const recentTotal = activity.reduce((sum, item) => sum + Number(item.total || 0), 0);
    const recentFailed = activity.reduce((sum, item) => sum + Number(item.failed || 0), 0);
    $("#adminActivitySummary").textContent = `${recentTotal} 个任务 · ${recentFailed} 个失败`;

    const active = Number(queue.running || 0) + Number(queue.queued || 0);
    const worker = queue.worker_alive ? "在线" : "离线";
    $("#adminHealthSummary").innerHTML = `
      <div><span class="health-dot ${queue.worker_alive ? "ok" : "bad"}"></span><strong>队列 Worker</strong><b>${worker}</b></div>
      <div><span class="health-dot ${active ? "busy" : "ok"}"></span><strong>待处理任务</strong><b>${active}</b></div>
      <div><span class="health-dot ok"></span><strong>活跃会话</strong><b>${Number(insights.active_sessions || 0)}</b></div>
      <small>最近任务 ${formatDate(insights.last_job_at)} · 最近操作 ${formatDate(insights.last_audit_at)}</small>
    `;
    const failures = insights.failure_reasons || [];
    $("#adminFailureReasons").innerHTML = failures.length
      ? `<h3>失败原因</h3>${failures.map((item) => `<div class="failure-row"><span>${escapeHtml(item.reason)}</span><strong>${item.count}</strong></div>`).join("")}`
      : '<p class="muted-empty">暂无失败任务</p>';
    $("#adminTopUsers").innerHTML = rankingRows(insights.top_users, (item) => `${item.jobs} 个任务 · ${item.completed} 成功 · ${item.failed} 失败`);
    $("#adminTopProjects").innerHTML = rankingRows(insights.top_projects, (item) => `${item.jobs} 个任务 · ${item.completed} 成功 · ${item.failed} 失败`);
  }

  #renderUsers(users) {
    const search = this.#state.userFilter.search.trim().toLowerCase();
    const status = this.#state.userFilter.status;
    const filtered = users.filter((user) => {
      if (status !== "all" && user.status !== status) return false;
      if (!search) return true;
      return `${user.display_name} ${user.username}`.toLowerCase().includes(search);
    });
    $("#adminUserSummary").textContent = `${filtered.length} / ${users.length} 个账户`;
    $("#adminUserTable").innerHTML = table(
      ["账户", "角色", "项目 / 任务", "资产", "状态", "最近登录 / 活动", "操作"],
      filtered.map((user) => [
        `<div class="table-identity"><span class="member-avatar">${escapeHtml(user.display_name.slice(0, 1))}</span><span><strong>${escapeHtml(user.display_name)}</strong><small>@${escapeHtml(user.username)}</small></span></div>`,
        user.role === "system_admin" ? "系统管理员" : "项目成员",
        `${user.project_count} 个项目<small>${user.job_count || 0} 个任务</small>`,
        `${user.voice_count || 0} 声音库<small>${user.script_count || 0} 份台本</small>`,
        `<span class="admin-status ${user.status}">${user.status === "active" ? "启用" : "停用"}</span>`,
        `${formatDate(user.last_login_at)}<small>活动 ${formatDate(user.last_activity_at)}</small>`,
        `<button class="table-action" data-user-action="reset" data-user-id="${escapeHtml(user.id)}" data-user-name="${escapeHtml(user.display_name)}">重置密码</button>${user.role !== "system_admin" ? `<button class="table-action" data-user-action="status" data-user-id="${escapeHtml(user.id)}" data-status="${user.status === "active" ? "disabled" : "active"}">${user.status === "active" ? "停用" : "启用"}</button>` : ""}`,
      ]),
    );
  }

  #renderProjects(projects) {
    const search = this.#state.projectFilter.search.trim().toLowerCase();
    const filtered = projects.filter((project) => {
      if (!search) return true;
      return `${project.name} ${project.owner_name} ${project.owner_username || ""}`.toLowerCase().includes(search);
    });
    $("#adminProjectSummary").textContent = `${filtered.length} / ${projects.length} 个项目`;
    $("#adminProjectTable").innerHTML = table(
      ["项目", "负责人", "成员", "声音库", "台本", "任务", "最近任务", "操作"],
      filtered.map((project) => [
        `<strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.description || "暂无说明")}</small>`,
        escapeHtml(project.owner_name),
        `${project.member_count}<small>${project.active_member_count || 0} 个启用</small>`,
        project.voice_count,
        project.script_count,
        project.job_count,
        formatDate(project.last_job_at),
        `<button class="table-action" type="button" data-project-detail="${escapeHtml(project.id)}">查看详情</button><button class="table-action" type="button" data-project-jobs="${escapeHtml(project.id)}">任务</button>`,
      ]),
    );
  }

  #renderAssetProjectOptions() {
    const select = $("#adminAssetProject");
    const current = this.#state.assetFilter.projectId;
    select.innerHTML = '<option value="all">全部项目</option>' + this.#state.projects
      .map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`)
      .join("");
    select.value = this.#state.projects.some((project) => project.id === current) ? current : "all";
  }

  #renderAssets() {
    const search = this.#state.assetFilter.search.trim().toLowerCase();
    const type = this.#state.assetFilter.type;
    const projectId = this.#state.assetFilter.projectId;
    const voices = (this.#state.assets.voices || []).map((item) => ({ ...item, assetType: "voice", typeLabel: "声音库" }));
    const scripts = (this.#state.assets.scripts || []).map((item) => ({ ...item, assetType: "script", typeLabel: "台本" }));
    const filtered = [...voices, ...scripts].filter((item) => {
      if (type !== "all" && item.assetType !== type) return false;
      if (projectId !== "all" && item.project_id !== projectId) return false;
      if (!search) return true;
      return `${item.name} ${item.original_name || ""} ${item.owner_name} ${item.project_name}`.toLowerCase().includes(search);
    });
    $("#adminAssetSummary").textContent = `${filtered.length} / ${voices.length + scripts.length} 项资产`;
    $("#adminAssetTable").innerHTML = table(
      ["类型", "资产", "项目", "负责人", "规模", "来源", "创建时间"],
      filtered.map((item) => [
        `<span class="asset-kind ${item.assetType}">${item.typeLabel}</span>`,
        `<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.original_name || item.id)}</small>`,
        escapeHtml(item.project_name || "未分配项目"),
        escapeHtml(item.owner_name || "—"),
        item.assetType === "voice"
          ? `${item.file_count} 段录音<small>${formatBytes(item.size_bytes)} · ${item.enabled_file_count} 启用</small>`
          : `${item.item_count} 段台词<small>${escapeHtml(item.source_kind || "文本")}</small>`,
        escapeHtml(item.source_kind || "—"),
        formatDate(item.created_at),
      ]),
    );
  }

  #renderJobs() {
    const search = this.#state.jobFilter.search.trim().toLowerCase();
    const status = this.#state.jobFilter.status;
    const projectId = this.#state.jobFilter.projectId;
    const filtered = this.#state.jobs.filter((job) => {
      if (status !== "all" && job.status !== status) return false;
      if (projectId && job.project_id !== projectId) return false;
      if (!search) return true;
      const haystack = [
        job.name,
        job.id,
        job.client_id,
        this.#userLabel(job.client_id),
        job.project_id,
        this.#projectLabel(job.project_id),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(search);
    });
    $("#adminJobSummary").textContent = `${filtered.length} / ${this.#state.jobs.length} 条任务`;
    $("#adminJobTable").innerHTML = table(
      ["任务", "提交用户", "项目", "模型", "进度", "状态", "提交时间", "操作"],
      filtered.map((job) => [
        `<strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(job.id)}</small>`,
        `<strong>${escapeHtml(this.#userLabel(job.client_id))}</strong><small>${escapeHtml(job.client_id)}</small>`,
        escapeHtml(this.#projectLabel(job.project_id)),
        escapeHtml(this.#modelLabel(job.model_id)),
        `<strong>${job.completed_items}/${job.total_items} 段</strong><small>${job.progress}%</small>`,
        `<span class="admin-status ${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</span>`,
        formatDate(job.submitted_at),
        `<button class="table-action" type="button" data-job-action="detail" data-job-id="${escapeHtml(job.id)}">查看详情</button>${job.status === "queued" || job.status === "running" ? "" : `<button class="table-action danger-action" type="button" data-job-action="delete" data-job-id="${escapeHtml(job.id)}">删除</button>`}`,
      ]),
    );
  }

  #userLabel(userId) {
    const user = this.#state.users.find((item) => String(item.id) === String(userId));
    return user ? `${user.display_name} @${user.username}` : userId || "—";
  }

  #projectLabel(projectId) {
    const project = this.#state.projects.find(
      (item) => String(item.id) === String(projectId),
    );
    return project ? project.name : projectId || "—";
  }

  #modelLabel(modelId) {
    const model = (this.#state.config.models || []).find(
      (item) => String(item.id) === String(modelId),
    );
    return model?.label || modelId || "—";
  }

  #renderLogs(logs) {
    const search = this.#state.logFilter.search.trim().toLowerCase();
    const result = this.#state.logFilter.result;
    const filtered = logs.filter((log) => {
      if (result === "success" && !log.success) return false;
      if (result === "failed" && log.success) return false;
      if (!search) return true;
      return `${log.actor_name || "系统"} ${log.action} ${log.target_type} ${log.target_id} ${log.project_id || ""}`.toLowerCase().includes(search);
    });
    $("#adminLogSummary").textContent = `${filtered.length} / ${logs.length} 条记录`;
    $("#adminLogTable").innerHTML = table(
      ["时间", "操作者", "动作", "对象", "项目", "来源", "结果"],
      filtered.map((log) => [
        formatDate(log.created_at),
        escapeHtml(log.actor_name || "系统"),
        escapeHtml(log.action),
        `${escapeHtml(log.target_type)} ${escapeHtml(log.target_id)}`,
        escapeHtml(log.project_id || "—"),
        escapeHtml(log.ip_address || "—"),
        `<span class="admin-status ${log.success ? "active" : "disabled"}" title="${escapeHtml(JSON.stringify(log.details || {}))}">${log.success ? "成功" : "失败"}</span>`,
      ]),
    );
  }

  #projectAction(event) {
    const detailButton = event.target.closest("[data-project-detail]");
    if (detailButton) {
      void this.#openProjectDetail(detailButton.dataset.projectDetail);
      return;
    }
    this.#jobAction(event);
  }

  async #openProjectDetail(projectId) {
    const dialog = $("#projectDetailDialog");
    const detail = $("#adminProjectDetail");
    $("#adminProjectDetailTitle").textContent = "读取项目…";
    detail.innerHTML = '<p class="muted-empty">正在读取项目详情…</p>';
    if (!dialog.open) dialog.showModal();
    try {
      const payload = await this.#api.get(`/api/admin/projects/${encodeURIComponent(projectId)}`);
      this.#renderProjectDetail(payload);
    } catch (error) {
      detail.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
      this.#shell.toast(error.message, true);
    }
  }

  #renderProjectDetail(payload) {
    const project = payload.project;
    $("#adminProjectDetailTitle").textContent = project.name;
    const members = payload.members || [];
    const voices = payload.voices || [];
    const scripts = payload.scripts || [];
    const jobs = payload.recent_jobs || [];
    $("#adminProjectDetail").innerHTML = `
      <div class="admin-project-summary">
        <div><span>负责人</span><strong>${escapeHtml(project.owner_name)}</strong></div>
        <div><span>成员</span><strong>${project.member_count} 人 · ${project.active_member_count || 0} 启用</strong></div>
        <div><span>资产</span><strong>${voices.length} 声音库 · ${scripts.length} 份台本</strong></div>
        <div><span>任务</span><strong>${project.job_count} 个 · 最近 ${formatDate(project.last_job_at)}</strong></div>
      </div>
      <div class="project-detail-grid">
        <section><header><h3>项目成员</h3><span>${members.length}</span></header><div class="project-member-list">${members.length ? members.map((member) => `<div><span class="member-avatar">${escapeHtml(member.display_name.slice(0, 1))}</span><strong>${escapeHtml(member.display_name)}</strong><small>${member.role === "owner" ? "负责人" : "成员"} · ${member.status === "active" ? "启用" : "停用"}</small></div>`).join("") : '<p class="muted-empty">暂无成员</p>'}</div></section>
        <section><header><h3>最近任务</h3><button type="button" data-project-jobs="${escapeHtml(project.id)}">查看全部</button></header><div class="project-job-list">${jobs.length ? jobs.slice(0, 6).map((job) => `<div><strong>${escapeHtml(job.name)}</strong><small>${statusLabel(job.status)} · ${formatDate(job.submitted_at)}</small></div>`).join("") : '<p class="muted-empty">暂无任务</p>'}</div></section>
      </div>
      <div class="project-asset-summary"><span>声音库 ${voices.length}</span><span>录音 ${voices.reduce((sum, voice) => sum + Number(voice.file_count || 0), 0)}</span><span>台本 ${scripts.length}</span><span>台词 ${scripts.reduce((sum, script) => sum + Number(script.item_count || 0), 0)}</span></div>
    `;
  }

  #jobAction(event) {
    const projectButton = event.target.closest("[data-project-jobs]");
    if (projectButton) {
      if ($("#projectDetailDialog").open) $("#projectDetailDialog").close();
      this.#state.jobFilter = {
        search: "",
        status: "all",
        projectId: projectButton.dataset.projectJobs,
      };
      $("#adminJobSearch").value = "";
      $("#adminJobStatus").value = "all";
      this.#showView("jobs");
      this.#renderJobs();
      return;
    }
    const button = event.target.closest("[data-job-action]");
    if (!button) return;
    if (button.dataset.jobAction === "detail") {
      void this.#openJobDetail(button.dataset.jobId);
    }
    if (button.dataset.jobAction === "delete") {
      void this.#deleteJob(button.dataset.jobId);
    }
    if (button.dataset.jobAction === "rename-save") {
      void this.#renameJob(button);
    }
  }

  async #openJobDetail(jobId) {
    const dialog = $("#jobDetailDialog");
    const detail = $("#adminJobDetail");
    $("#adminJobDetailTitle").textContent = "读取任务…";
    detail.innerHTML = '<p class="muted-empty">正在读取任务详情…</p>';
    if (!dialog.open) dialog.showModal();
    try {
      const payload = await this.#api.get(`/api/admin/jobs/${encodeURIComponent(jobId)}`);
      this.#renderJobDetail(payload.job);
    } catch (error) {
      detail.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
      this.#shell.toast(error.message, true);
    }
  }

  #renderJobDetail(job) {
    $("#adminJobDetailTitle").textContent = job.name;
    const links = [
      job.download_url ? `<a href="${escapeHtml(job.download_url)}" download>全部下载</a>` : "",
      job.can_export ? `<a href="${escapeHtml(job.export_url)}" download>正式导出</a>` : "",
    ].filter(Boolean).join("");
    const items = (job.items || []).map((item) => [
      `<strong>#${item.sequence}</strong><small>${escapeHtml(item.text)}</small>`,
      `<span class="admin-status ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span>${item.error ? `<small>${escapeHtml(item.error)}</small>` : ""}`,
      `${(item.candidates || []).length} 个候选`,
      item.audio_url
        ? `<audio controls preload="none" src="${escapeHtml(item.audio_url)}"></audio><a class="table-action" href="${escapeHtml(item.download_url || item.audio_url)}" download>下载</a>`
        : "暂无音频",
    ]);
    $("#adminJobDetail").innerHTML = `
      <div class="admin-job-summary">
        <div><span>提交用户</span><strong>${escapeHtml(this.#userLabel(job.client_id))}</strong></div>
        <div><span>项目</span><strong>${escapeHtml(this.#projectLabel(job.project_id))}</strong></div>
        <div><span>声音 / 模型</span><strong>${escapeHtml(job.voice_name)} · ${escapeHtml(this.#modelLabel(job.model_id))}</strong></div>
        <div><span>进度</span><strong>${job.completed_items}/${job.total_items} 段 · ${job.progress}%</strong></div>
      </div>
      <div class="admin-job-actions">
        <label class="admin-job-rename"><span>任务名称</span><input id="adminJobRenameInput" value="${escapeHtml(job.name)}" maxlength="80"></label>
        <button type="button" data-job-action="rename-save" data-job-id="${escapeHtml(job.id)}">保存名称</button>
        ${job.status === "queued" || job.status === "running" ? "" : `<button class="danger-action" type="button" data-job-action="delete" data-job-id="${escapeHtml(job.id)}">删除记录</button>`}
        ${links}
      </div>
      ${job.error ? `<pre class="admin-job-error">${escapeHtml(job.error)}</pre>` : ""}
      <div class="admin-job-items">${table(["序号 / 台本", "状态", "候选", "音频"], items)}</div>
    `;
  }

  async #renameJob(button) {
    const input = $("#adminJobRenameInput");
    const name = input.value.trim();
    if (!name) {
      this.#shell.toast("任务名称不能为空", true);
      return;
    }
    button.disabled = true;
    try {
      const payload = await this.#api.patch(
        `/api/admin/jobs/${encodeURIComponent(button.dataset.jobId)}`,
        { name },
      );
      const index = this.#state.jobs.findIndex(
        (job) => String(job.id) === String(button.dataset.jobId),
      );
      if (index >= 0) this.#state.jobs[index] = { ...this.#state.jobs[index], ...payload.job };
      this.#renderJobs();
      this.#renderJobDetail(payload.job);
      this.#shell.toast("任务名称已更新");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async #deleteJob(jobId) {
    const job = this.#state.jobs.find((item) => String(item.id) === String(jobId));
    if (!job || job.status === "queued" || job.status === "running") return;
    if (!window.confirm(`确定删除生成记录“${job.name}”吗？`)) return;
    try {
      await this.#api.delete(`/api/admin/jobs/${encodeURIComponent(jobId)}`);
      this.#state.jobs = this.#state.jobs.filter((item) => String(item.id) !== String(jobId));
      if ($("#jobDetailDialog").open) $("#jobDetailDialog").close();
      this.#renderJobs();
      this.#shell.toast("生成记录已删除");
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
  }

  async #createUser(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      await this.#api.post("/api/admin/users", {
        username: $("#newUsername").value.trim(),
        display_name: $("#newDisplayName").value.trim(),
        password: $("#newUserPassword").value,
      });
      form.reset();
      $("#userDialog").close();
      await this.#loadAll(true);
      this.#shell.toast("账户已创建");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  #userAction(event) {
    const button = event.target.closest("[data-user-action]");
    if (!button) return;
    if (button.dataset.userAction === "reset") {
      this.#resetUserId = button.dataset.userId;
      $("#resetPasswordTarget").textContent = `为 ${button.dataset.userName} 设置新的临时密码。`;
      $("#resetPasswordDialog").showModal();
    }
    if (button.dataset.userAction === "status") this.#toggleStatus(button);
  }

  async #toggleStatus(button) {
    try {
      await this.#api.patch(`/api/admin/users/${encodeURIComponent(button.dataset.userId)}/status`, { status: button.dataset.status });
      await this.#loadAll(true);
      this.#shell.toast(button.dataset.status === "active" ? "账户已启用" : "账户已停用");
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
  }

  async #resetPassword(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      await this.#api.post(`/api/admin/users/${encodeURIComponent(this.#resetUserId)}/reset-password`, { password: $("#resetPassword").value });
      form.reset();
      $("#resetPasswordDialog").close();
      await this.#loadAll(true);
      this.#shell.toast("临时密码已重置");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }
}

function table(headers, rows) {
  return `<table class="admin-table"><thead><tr>${headers.map((item) => `<th>${item}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><span class="muted-empty">暂无数据</span></td></tr>`}</tbody></table>`;
}

function auditRow(item) {
  return `<div class="audit-row"><span class="audit-dot ${item.success ? "ok" : "bad"}"></span><div><strong>${escapeHtml(item.actor_name || "系统")} · ${escapeHtml(item.action)}</strong><small>${formatDate(item.created_at)} · ${escapeHtml(item.ip_address || "本机")}</small></div></div>`;
}

function rankingRows(items = [], detail) {
  if (!items.length) return '<p class="muted-empty">暂无数据</p>';
  const max = Math.max(1, ...items.map((item) => Number(item.jobs || 0)));
  return items
    .map(
      (item) =>
        `<div class="ranking-row"><div><strong>${escapeHtml(item.name || "未命名")}</strong><small>${escapeHtml(detail(item))}</small></div><span style="--rank-width:${Math.round((Number(item.jobs || 0) / max) * 100)}%"><i></i><b>${item.jobs}</b></span></div>`,
    )
    .join("");
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function statusLabel(status) {
  return { queued: "排队中", running: "生成中", completed: "已完成", failed: "失败" }[status] || status;
}

new AdminApp().start();
