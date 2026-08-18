import { $, escapeHtml, renderSelect, setButtonBusy } from "../core/dom.js";

export class ProjectController {
  #state;
  #api;
  #shell;
  #onProjectChanged;
  #selectionVersion = 0;
  #memberLoadVersion = 0;

  constructor({ state, api, shell, onProjectChanged }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#onProjectChanged = onProjectChanged;
  }

  bind() {
    $("#projectSelect").addEventListener("change", (event) => {
      this.select(event.target.value);
    });
    $("#createProject").addEventListener("click", () => {
      $("#projectDialog").showModal();
      $("#projectName").focus();
    });
    $("#projectCreateForm").addEventListener("submit", (event) => this.#create(event));
    $("#memberAddForm").addEventListener("submit", (event) => this.#addMember(event));
    $("#leaveProject").addEventListener("click", () => this.#leave());
    $("#memberList").addEventListener("click", (event) => this.#removeMember(event));
  }

  async load() {
    const version = ++this.#selectionVersion;
    const payload = await this.#api.get("/api/projects");
    if (version !== this.#selectionVersion) return this.#state.project;
    this.#state.projects = payload.projects;
    if (!this.#state.projects.some((item) => item.id === this.#state.projectId)) {
      this.#state.selectProject(this.#state.projects[0]?.id || null);
    }
    await this.#render();
    return this.#state.project;
  }

  async select(projectId) {
    if (!projectId || projectId === this.#state.projectId) return;
    const version = ++this.#selectionVersion;
    this.#state.selectProject(projectId);
    await this.#render();
    if (version !== this.#selectionVersion) return;
    await this.#onProjectChanged(this.#state.project);
  }

  async refreshCurrent() {
    const projectId = this.#state.projectId;
    if (!projectId) return;
    const { project } = await this.#api.get(
      `/api/projects/${encodeURIComponent(projectId)}`,
    );
    if (this.#state.projectId !== projectId) return;
    const index = this.#state.projects.findIndex((item) => item.id === project.id);
    if (index >= 0) this.#state.projects[index] = project;
    await this.#render();
  }

  async #render() {
    const memberLoadVersion = ++this.#memberLoadVersion;
    renderSelect(
      $("#projectSelect"),
      this.#state.projects,
      "选择项目",
      (item) => item.name,
    );
    $("#projectSelect").value = this.#state.projectId || "";
    this.#renderProjectCards();
    const project = this.#state.project;
    $("#emptyProjectState").hidden = Boolean(project);
    $("#projectContent").hidden = !project;
    $("#openCreate").disabled = !project;
    $("#memberAddForm").hidden = !project?.can_manage;
    $("#leaveProject").hidden = !project || project.member_role === "owner";
    if (!project) return;
    $("#currentProjectName").textContent = project.name;
    $("#currentProjectDescription").textContent = project.description || "暂无项目说明";
    $("#projectMemberCount").textContent = project.member_count;
    $("#projectVoiceCount").textContent = project.voice_count;
    $("#projectScriptCount").textContent = project.script_count;
    $("#projectJobCount").textContent = project.job_count;
    await this.#loadMembers(memberLoadVersion, project.id);
  }

  #renderProjectCards() {
    $("#projectGrid").innerHTML = this.#state.projects.map((project) => `
      <button class="project-card${project.id === this.#state.projectId ? " active" : ""}"
              type="button" data-project-id="${escapeHtml(project.id)}">
        <span class="project-card-mark">${escapeHtml(project.name.slice(0, 1))}</span>
        <span><strong>${escapeHtml(project.name)}</strong><small>${project.member_count} 位成员 · ${project.job_count} 个任务</small></span>
      </button>
    `).join("");
    $("#projectGrid").querySelectorAll("[data-project-id]").forEach((button) => {
      button.addEventListener("click", () => this.select(button.dataset.projectId));
    });
  }

  async #loadMembers(version, projectId) {
    const { members } = await this.#api.get(
      `/api/projects/${encodeURIComponent(projectId)}/members`,
    );
    if (version !== this.#memberLoadVersion || this.#state.projectId !== projectId) return;
    const canManage = Boolean(this.#state.project?.can_manage);
    $("#memberList").innerHTML = members.map((member) => `
      <article class="member-row">
        <span class="member-avatar">${escapeHtml(member.display_name.slice(0, 1))}</span>
        <div><strong>${escapeHtml(member.display_name)}</strong><small>@${escapeHtml(member.username)}</small></div>
        <em>${member.role === "owner" ? "负责人" : "成员"}</em>
        ${canManage && member.role !== "owner"
          ? `<button class="icon-button" type="button" title="移除成员" data-remove-member="${escapeHtml(member.id)}">×</button>`
          : ""}
      </article>
    `).join("");
  }

  async #create(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      const { project } = await this.#api.post("/api/projects", {
        name: $("#projectName").value.trim(),
        description: $("#projectDescription").value.trim(),
      });
      form.reset();
      $("#projectDialog").close();
      await this.load();
      if (this.#state.projectId !== project.id) {
        await this.select(project.id);
      } else {
        await this.#onProjectChanged(this.#state.project);
      }
      this.#shell.showView("project");
      this.#shell.toast("项目已创建");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #addMember(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      await this.#api.post(
        `/api/projects/${encodeURIComponent(this.#state.projectId)}/members`,
        { username: $("#memberUsername").value.trim() },
      );
      form.reset();
      await this.refreshCurrent();
      this.#shell.toast("成员已添加");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #removeMember(event) {
    const button = event.target.closest("[data-remove-member]");
    if (!button) return;
    setButtonBusy(button, true);
    try {
      await this.#api.delete(
        `/api/projects/${encodeURIComponent(this.#state.projectId)}/members/${encodeURIComponent(button.dataset.removeMember)}`,
      );
      await this.refreshCurrent();
      this.#shell.toast("成员已移除");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #leave() {
    const project = this.#state.project;
    const userId = this.#state.user?.id;
    if (!project || project.member_role === "owner" || !userId) return;
    if (!window.confirm(`退出“${project.name}”后将无法访问其中的资产，确定退出吗？`)) {
      return;
    }
    const button = $("#leaveProject");
    setButtonBusy(button, true);
    try {
      await this.#api.delete(
        `/api/projects/${encodeURIComponent(project.id)}/members/${encodeURIComponent(userId)}`,
      );
      await this.load();
      await this.#onProjectChanged(this.#state.project);
      this.#shell.showView("project");
      this.#shell.toast("已退出项目");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }
}
