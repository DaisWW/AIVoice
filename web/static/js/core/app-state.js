const KEYS = {
  view: "voice-lab-view",
  job: "voice-lab-job",
  voice: "voice-lab-voice",
};

export class AppState {
  #storage;

  constructor(location = window.location) {
    this.#storage = this.#resolveStorage();
    this.isAdmin = location.pathname === "/admin" || location.pathname.startsWith("/admin/");
    this.clientId = "";
    this.config = { models: [] };
    this.voices = [];
    this.scripts = [];
    this.jobs = [];
    this.filter = "all";
    this.scriptMode = "existing";
    this.view = this.#read(KEYS.view) === "library" ? "library" : "workspace";
    this.selectedJobId = this.#read(KEYS.job);
    this.selectedVoiceId = this.#read(KEYS.voice);
    this.voiceDetail = null;
  }

  get jobApiBase() {
    return this.isAdmin ? "/api/admin/jobs" : "/api/jobs";
  }

  setView(view) {
    this.view = view;
    this.#write(KEYS.view, view);
  }

  selectJob(jobId) {
    this.selectedJobId = jobId;
    this.#write(KEYS.job, jobId);
  }

  selectVoice(voiceId) {
    this.selectedVoiceId = voiceId;
    this.#write(KEYS.voice, voiceId);
  }

  model(modelId) {
    return this.config.models.find((item) => item.id === modelId);
  }

  #resolveStorage() {
    try {
      return window.sessionStorage;
    } catch (_) {
      return null;
    }
  }

  #read(key) {
    try {
      return this.#storage?.getItem(key) || null;
    } catch (_) {
      return null;
    }
  }

  #write(key, value) {
    try {
      if (value) this.#storage?.setItem(key, value);
      else this.#storage?.removeItem(key);
    } catch (_) {
      // Restricted browser modes may disable session storage.
    }
  }
}
