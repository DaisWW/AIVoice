import { $, $$ } from "../core/dom.js";

export class ShellController {
  #state;
  #toastTimer = null;
  #onViewChange = () => {};

  constructor(state) {
    this.#state = state;
  }

  bind(onViewChange) {
    this.#onViewChange = onViewChange;
    $("#workspaceNav").addEventListener("click", () => this.showView("workspace"));
    $("#libraryNav").addEventListener("click", () => this.showView("library"));
    $("#projectNav").addEventListener("click", () => this.showView("project"));
    $$('[data-close-dialog]').forEach((button) => {
      button.addEventListener("click", () => $(`#${button.dataset.closeDialog}`).close());
    });
  }

  showView(name) {
    const resolved = ["workspace", "library", "project"].includes(name)
      ? name
      : "workspace";
    const changed = this.#state.view !== resolved;
    this.#state.setView(resolved);
    const workspace = resolved === "workspace";
    const library = resolved === "library";
    const project = resolved === "project";
    $(".app-shell").classList.toggle("section-mode", !workspace);
    this.#toggleView($("#workspaceView"), workspace);
    this.#toggleView($("#libraryView"), library);
    this.#toggleView($("#projectView"), project);
    this.#toggleNav($("#workspaceNav"), workspace);
    this.#toggleNav($("#libraryNav"), library);
    this.#toggleNav($("#projectNav"), project);
    if (changed && window.matchMedia("(max-width: 760px)").matches) {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    this.#onViewChange(resolved);
  }

  renderIdentity(user) {
    this.#state.user = user;
    this.#state.isAdmin = user.role === "system_admin";
    $("#identityRole").textContent = user.role === "system_admin" ? "系统管理员" : "项目成员";
    $("#clientId").textContent = user.display_name;
    $("#userAvatar").textContent = user.display_name.slice(0, 1);
    $("#jobListTitle").textContent = "项目生成记录";
    $("#adminLink").hidden = !this.#state.isAdmin;
  }

  renderHealth(health) {
    const active = ["running", "queued", "candidate_running", "candidate_queued"]
      .reduce((total, key) => total + Number(health.queue[key] || 0), 0);
    $("#workerDot").classList.toggle("online", Boolean(health.queue.worker_alive));
    $("#queueSummary").textContent = active ? `${active} 个任务处理中` : "队列空闲";
  }

  renderOffline() {
    $("#workerDot")?.classList.remove("online");
    if ($("#queueSummary")) $("#queueSummary").textContent = "连接断开";
  }

  toast(message, error = false) {
    const element = $("#toast");
    element.textContent = message;
    element.classList.toggle("error", error);
    element.classList.add("show");
    clearTimeout(this.#toastTimer);
    this.#toastTimer = setTimeout(() => element.classList.remove("show"), 3200);
  }

  reveal() {
    requestAnimationFrame(() => document.documentElement.classList.remove("app-loading"));
  }

  #toggleView(view, active) {
    view.classList.toggle("active", active);
    view.setAttribute("aria-hidden", String(!active));
  }

  #toggleNav(button, active) {
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}
