import { AuthController } from "./auth/auth-controller.js?v=20260815";
import { ApiClient } from "./core/api-client.js";
import { $, escapeHtml, setButtonBusy } from "./core/dom.js";
import { ProviderController } from "./providers/provider-controller.js";

const TITLES = {
  overview: ["SYSTEM OVERVIEW", "系统概览"],
  users: ["IDENTITIES", "账户管理"],
  projects: ["WORKSPACES", "全部项目"],
  jobs: ["GENERATION QUEUE", "生成任务"],
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
  #state = {
    isAdmin: false,
    config: { models: [] },
    users: [],
    projects: [],
    jobs: [],
    jobFilter: { search: "", status: "all", projectId: "" },
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
    $("#adminRefresh").addEventListener("click", () => this.#loadAll());
    $("#openUserDialog").addEventListener("click", () => $("#userDialog").showModal());
    $("#userCreateForm").addEventListener("submit", (event) => this.#createUser(event));
    $("#resetPasswordForm").addEventListener("submit", (event) => this.#resetPassword(event));
    $("#adminUserTable").addEventListener("click", (event) => this.#userAction(event));
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
    $("#adminProjectTable").addEventListener("click", (event) => this.#jobAction(event));
    $("#adminJobDetail").addEventListener("click", (event) => this.#jobAction(event));
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

  async #loadAll() {
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
      this.#renderUsers(this.#state.users);
      this.#renderProjects(this.#state.projects);
      this.#renderJobs();
      this.#renderLogs(logs.logs);
      this.#providers.renderLocalModels();
      await this.#providers.ensureLoaded();
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
    await overviewPromise;
  }

  #showView(name) {
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
  }

  #renderUsers(users) {
    $("#adminUserTable").innerHTML = table(
      ["账户", "角色", "项目", "状态", "最近登录", "操作"],
      users.map((user) => [
        `<div class="table-identity"><span class="member-avatar">${escapeHtml(user.display_name.slice(0, 1))}</span><span><strong>${escapeHtml(user.display_name)}</strong><small>@${escapeHtml(user.username)}</small></span></div>`,
        user.role === "system_admin" ? "系统管理员" : "项目成员",
        `${user.project_count} 个项目`,
        `<span class="admin-status ${user.status}">${user.status === "active" ? "启用" : "停用"}</span>`,
        formatDate(user.last_login_at),
        `<button class="table-action" data-user-action="reset" data-user-id="${escapeHtml(user.id)}" data-user-name="${escapeHtml(user.display_name)}">重置密码</button>${user.role !== "system_admin" ? `<button class="table-action" data-user-action="status" data-user-id="${escapeHtml(user.id)}" data-status="${user.status === "active" ? "disabled" : "active"}">${user.status === "active" ? "停用" : "启用"}</button>` : ""}`,
      ]),
    );
  }

  #renderProjects(projects) {
    $("#adminProjectTable").innerHTML = table(
      ["项目", "负责人", "成员", "声音库", "台本", "任务", "创建时间", "操作"],
      projects.map((project) => [
        `<strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.description || "暂无说明")}</small>`,
        escapeHtml(project.owner_name),
        project.member_count,
        project.voice_count,
        project.script_count,
        project.job_count,
        formatDate(project.created_at),
        `<button class="table-action" type="button" data-project-jobs="${escapeHtml(project.id)}">查看任务</button>`,
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
        `<button class="table-action" type="button" data-job-action="detail" data-job-id="${escapeHtml(job.id)}">查看详情</button>`,
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
    $("#adminLogTable").innerHTML = table(
      ["时间", "操作者", "动作", "对象", "项目", "来源", "结果"],
      logs.map((log) => [
        formatDate(log.created_at),
        escapeHtml(log.actor_name || "系统"),
        escapeHtml(log.action),
        `${escapeHtml(log.target_type)} ${escapeHtml(log.target_id)}`,
        escapeHtml(log.project_id || "—"),
        escapeHtml(log.ip_address || "—"),
        `<span class="admin-status ${log.success ? "active" : "disabled"}">${log.success ? "成功" : "失败"}</span>`,
      ]),
    );
  }

  #jobAction(event) {
    const projectButton = event.target.closest("[data-project-jobs]");
    if (projectButton) {
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
      await this.#loadAll();
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
      await this.#loadAll();
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
      await this.#loadAll();
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
