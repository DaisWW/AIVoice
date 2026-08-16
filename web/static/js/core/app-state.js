const KEYS = {
  view: "voice-lab-view",
  job: "voice-lab-job",
  voice: "voice-lab-voice",
  project: "voice-lab-project",
};

export class AppState {
  #storage;

  constructor() {
    this.#storage = this.#resolveStorage();
    this.user = null;
    this.isAdmin = false;
    this.projects = [];
    this.invitations = [];
    this.projectId = this.#read(KEYS.project);
    this.config = { models: [] };
    this.voices = [];
    this.scripts = [];
    this.jobs = [];
    this.filter = "all";
    this.scriptMode = "existing";
    const storedView = this.#read(KEYS.view);
    this.view = ["workspace", "library", "project"].includes(storedView)
      ? storedView
      : "workspace";
    this.selectedJobId = this.#read(KEYS.job);
    this.selectedVoiceId = this.#read(KEYS.voice);
    this.voiceDetail = null;
  }

  get jobApiBase() {
    return "/api/jobs";
  }

  get project() {
    return this.projects.find((item) => item.id === this.projectId) || null;
  }

  setView(view) {
    this.view = view;
    this.#write(KEYS.view, view);
  }

  selectProject(projectId) {
    this.projectId = projectId || null;
    this.#write(KEYS.project, this.projectId);
    this.selectJob(null);
    this.selectVoice(null);
    this.voiceDetail = null;
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
