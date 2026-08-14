import { ApiClient } from "./core/api-client.js";
import { CandidateController } from "./candidates/candidate-controller.js";
import { AppState } from "./core/app-state.js";
import { AudioLoader } from "./core/audio-loader.js";
import { GenerationController } from "./generation/generation-controller.js";
import { JobController } from "./jobs/job-controller.js";
import { JobDetailView } from "./jobs/job-detail-view.js";
import { JobListView } from "./jobs/job-list-view.js";
import { SystemController } from "./system/system-controller.js";
import { ShellController } from "./ui/shell-controller.js";
import { VoiceController } from "./voices/voice-controller.js";
import { VoiceDetailView } from "./voices/voice-detail-view.js";
import { VoiceListView } from "./voices/voice-list-view.js";

class VoiceLabApp {
  #state = new AppState();
  #api = new ApiClient();
  #audioLoader = new AudioLoader();
  #shell = new ShellController(this.#state);
  #system = new SystemController(this.#api, this.#shell);
  #jobs;
  #voices;
  #generation;
  #candidates;

  constructor() {
    this.#jobs = new JobController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      listView: new JobListView(this.#state),
      detailView: new JobDetailView(this.#state, this.#audioLoader),
    });
    this.#voices = new VoiceController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      listView: new VoiceListView(this.#state),
      detailView: new VoiceDetailView(this.#state, this.#audioLoader),
      onVoicesChanged: () => this.#generation?.renderOptions(),
    });
    this.#generation = new GenerationController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      jobs: this.#jobs,
      onManageVoices: () => this.#shell.showView("library"),
    });
    this.#candidates = new CandidateController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      jobs: this.#jobs,
    });
    this.#jobs.setCandidateController(this.#candidates);
  }

  async start() {
    this.#bind();
    this.#generation.setScriptMode("existing");
    this.#shell.showView(this.#state.view);
    try {
      await this.#system.loadIdentity();
      this.#loadState(await this.#fetchInitialData());
      this.#render();
      this.#system.refreshHealth();
      await this.#jobs.selectInitial();
      if (this.#state.view === "library") await this.#voices.ensureSelection();
    } catch (error) {
      this.#shell.toast(error.message, true);
      this.#shell.renderOffline();
    } finally {
      this.#shell.reveal();
    }
    this.#jobs.schedule();
    this.#system.start();
  }

  #bind() {
    this.#shell.bind((view) => {
      if (view === "library") this.#voices.ensureSelection();
    });
    this.#jobs.bind();
    this.#voices.bind();
    this.#generation.bind();
    this.#candidates.bind();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) return;
      Promise.all([this.#jobs.onVisible(), this.#system.refreshHealth()]);
    });
  }

  async #fetchInitialData() {
    return Promise.all([
      this.#api.get("/api/config"),
      this.#api.get("/api/voices"),
      this.#api.get("/api/scripts"),
      this.#api.get(`${this.#state.jobApiBase}?limit=300`),
    ]);
  }

  #loadState([config, voices, scripts, jobs]) {
    this.#state.config = config;
    this.#state.voices = voices.voices;
    this.#state.scripts = scripts.scripts;
    this.#state.jobs = jobs.jobs;
  }

  #render() {
    this.#generation.renderOptions();
    this.#generation.applyScriptDefaults();
    this.#voices.render();
    this.#jobs.render();
  }
}

new VoiceLabApp().start();
