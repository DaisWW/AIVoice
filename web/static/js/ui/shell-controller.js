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
    $$("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => $(
        `#${button.dataset.closeDialog}`,
      ).close());
    });
  }

  showView(name) {
    const changed = this.#state.view !== name;
    this.#state.setView(name);
    const workspaceActive = name === "workspace";
    $(".app-shell").classList.toggle("library-mode", !workspaceActive);
    this.#toggleView($("#workspaceView"), workspaceActive);
    this.#toggleView($("#libraryView"), !workspaceActive);
    this.#toggleNav($("#workspaceNav"), workspaceActive);
    this.#toggleNav($("#libraryNav"), !workspaceActive);
    if (changed && window.matchMedia("(max-width: 760px)").matches) {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    this.#onViewChange(name);
  }

  renderIdentity(identity) {
    const admin = this.#state.isAdmin;
    this.#state.clientId = identity.client_id;
    $("#clientId").textContent = admin ? "全部用户任务" : identity.client_id;
    $("#identityRole").textContent = admin ? "服务器管理员" : "匿名工作区";
    $("#jobListTitle").textContent = admin ? "全部生成记录" : "我的生成记录";
    const link = $("#adminLink");
    if (admin) {
      link.hidden = false;
      link.href = "/";
      link.textContent = "返回个人视图";
    } else if (identity.admin_available) {
      link.hidden = false;
    }
  }

  renderHealth(health) {
    const active = ["running", "queued", "candidate_running", "candidate_queued"]
      .reduce((total, key) => total + Number(health.queue[key] || 0), 0);
    $("#workerDot").classList.toggle("online", Boolean(health.queue.worker_alive));
    $("#queueSummary").textContent = active ? `${active} 个任务处理中` : "队列空闲";
  }

  renderOffline() {
    $("#workerDot").classList.remove("online");
    $("#queueSummary").textContent = "连接断开";
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
