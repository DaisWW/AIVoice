import { $, setButtonBusy } from "../core/dom.js";

export class AuthController {
  #api;
  #shellId;
  #onAuthenticated;

  constructor(api, shellId, onAuthenticated) {
    this.#api = api;
    this.#shellId = shellId;
    this.#onAuthenticated = onAuthenticated;
  }

  bind() {
    $("#loginForm").addEventListener("submit", (event) => this.#login(event));
    $("#logoutButton").addEventListener("click", () => this.#logout());
    $("#passwordForm")?.addEventListener("submit", (event) => this.#changePassword(event));
  }

  async restore() {
    try {
      const { user } = await this.#api.get("/api/auth/session");
      this.showAuthenticated(user);
      return user;
    } catch (error) {
      if (error.status !== 401) this.#setError(error.message);
      this.showLogin();
      return null;
    }
  }

  showAuthenticated(user) {
    $("#authView").hidden = true;
    $(`#${this.#shellId}`).hidden = false;
    $("#createDrawer")?.toggleAttribute("hidden", false);
    document.body.classList.add("authenticated");
    if (user.must_change_password) $("#passwordDialog")?.showModal();
  }

  showLogin() {
    const drawer = $("#createDrawer");
    const backdrop = $("#drawerBackdrop");
    drawer?.classList.remove("open");
    drawer?.setAttribute("aria-hidden", "true");
    drawer?.toggleAttribute("hidden", true);
    if (backdrop) {
      backdrop.classList.remove("visible");
      backdrop.hidden = true;
    }
    $("#authView").hidden = false;
    $(`#${this.#shellId}`).hidden = true;
    document.body.classList.remove("authenticated");
    requestAnimationFrame(() => $("#loginUsername").focus());
  }

  async #login(event) {
    event.preventDefault();
    const button = $("button[type='submit']", event.currentTarget);
    setButtonBusy(button, true);
    this.#setError("");
    try {
      const { user } = await this.#api.post("/api/auth/login", {
        username: $("#loginUsername").value.trim(),
        password: $("#loginPassword").value,
      });
      $("#loginPassword").value = "";
      this.showAuthenticated(user);
      await this.#onAuthenticated(user);
    } catch (error) {
      this.#setError(error.message);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #logout() {
    try {
      await this.#api.post("/api/auth/logout", {});
    } finally {
      window.location.assign("/");
    }
  }

  async #changePassword(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    const message = $("#passwordMessage");
    setButtonBusy(button, true);
    message.textContent = "";
    try {
      await this.#api.post("/api/auth/change-password", {
        current_password: $("#currentPassword").value,
        new_password: $("#newPassword").value,
      });
      form.reset();
      $("#passwordDialog").close();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      setButtonBusy(button, false);
    }
  }

  #setError(message) {
    $("#loginError").textContent = message;
  }
}
