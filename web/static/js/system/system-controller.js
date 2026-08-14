export class SystemController {
  #api;
  #shell;
  #timer = null;

  constructor(api, shell) {
    this.#api = api;
    this.#shell = shell;
  }

  async loadIdentity() {
    const identity = await this.#api.get("/api/identity");
    this.#shell.renderIdentity(identity);
    return identity;
  }

  async refreshHealth() {
    try {
      const health = await this.#api.get("/api/health");
      this.#shell.renderHealth(health);
      return health;
    } catch (_) {
      this.#shell.renderOffline();
      return null;
    }
  }

  start() {
    clearInterval(this.#timer);
    this.#timer = setInterval(() => {
      if (!document.hidden) this.refreshHealth();
    }, 8000);
  }
}
