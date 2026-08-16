import { $, escapeHtml, renderSelect, setButtonBusy } from "../core/dom.js";

export class ProjectController {
  #state;
  #api;
  #shell;
  #onProjectChanged;
  #members = [];

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
    $("#inviteForm").addEventListener("submit", (event) => this.#invite(event));
    $("#invitationList").addEventListener("click", (event) => this.#respond(event));
    $("#memberList").addEventListener("click", (event) => this.#removeMember(event));
  }

  async load() {
    const payload = await this.#api.get("/api/projects");
    this.#state.projects = payload.projects;
    this.#state.invitations = payload.invitations;
    if (!this.#state.projects.some((item) => item.id === this.#state.projectId)) {
      this.#state.selectProject(this.#state.projects[0]?.id || null);
    }
    await this.#render();
    return this.#state.project;
  }

  async select(projectId) {
    if (!projectId || projectId === this.#state.projectId) return;
    this.#state.selectProject(projectId);
    await this.#render();
    await this.#onProjectChanged(this.#state.project);
  }

  async refreshCurrent() {
    if (!this.#state.projectId) return;
    const { project } = await this.#api.get(
      `/api/projects/${encodeURIComponent(this.#state.projectId)}`,
    );
    const index = this.#state.projects.findIndex((item) => item.id === project.id);
    if (index >= 0) this.#state.projects[index] = project;
    await this.#render();
  }

  async #render() {
    renderSelect(
      $("#projectSelect"),
      this.#state.projects,
      "选择项目",
      (item) => item.name,
    );
    $("#projectSelect").value = this.#state.projectId || "";
    this.#renderProjectCards();
    this.#renderInvitations();
    const project = this.#state.project;
    $("#emptyProjectState").hidden = Boolean(project);
    $("#projectContent").hidden = !project;
    $("#openCreate").disabled = !project;
    if (!project) return;
    $("#currentProjectName").textContent = project.name;
    $("#currentProjectDescription").textContent = project.description || "暂无项目说明";
    $("#projectMemberCount").textContent = project.member_count;
    $("#projectVoiceCount").textContent = project.voice_count;
    $("#projectScriptCount").textContent = project.script_count;
    $("#projectJobCount").textContent = project.job_count;
    $("#invitePanel").hidden = !project.can_manage;
    await this.#loadMembers();
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

  #renderInvitations() {
    const invitations = this.#state.invitations;
    $("#invitationBadge").textContent = invitations.length;
    $("#invitationBadge").hidden = invitations.length === 0;
    $("#invitationList").innerHTML = invitations.length
      ? invitations.map((item) => `
        <article class="invitation-card">
          <div><strong>${escapeHtml(item.project_name)}</strong><small>${escapeHtml(item.inviter_name)} 邀请你加入</small></div>
          <div>
            <button class="quiet-button" type="button" data-invite-action="decline" data-invite-id="${escapeHtml(item.id)}">忽略</button>
            <button class="primary-button" type="button" data-invite-action="accept" data-invite-id="${escapeHtml(item.id)}">加入项目</button>
          </div>
        </article>
      `).join("")
      : '<p class="muted-empty">当前没有待处理邀请</p>';
  }

  async #loadMembers() {
    const { members } = await this.#api.get(
      `/api/projects/${encodeURIComponent(this.#state.projectId)}/members`,
    );
    this.#members = members;
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

  async #invite(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      await this.#api.post(
        `/api/projects/${encodeURIComponent(this.#state.projectId)}/invitations`,
        { username: $("#inviteUsername").value.trim() },
      );
      form.reset();
      this.#shell.toast("邀请已发送");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #respond(event) {
    const button = event.target.closest("[data-invite-action]");
    if (!button) return;
    setButtonBusy(button, true);
    try {
      const { invitation } = await this.#api.post(
        `/api/invitations/${encodeURIComponent(button.dataset.inviteId)}/${button.dataset.inviteAction}`,
        {},
      );
      await this.load();
      if (button.dataset.inviteAction === "accept") {
        if (this.#state.projectId !== invitation.project_id) {
          await this.select(invitation.project_id);
        } else {
          await this.#onProjectChanged(this.#state.project);
        }
      }
      this.#shell.toast(button.dataset.inviteAction === "accept" ? "已加入项目" : "已忽略邀请");
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
      await this.#loadMembers();
      this.#shell.toast("成员已移除");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }
}
