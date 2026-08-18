import { ApiClient } from "./core/api-client.js";
import { CandidateController } from "./candidates/candidate-controller.js";
import { AppState } from "./core/app-state.js?v=20260817.1";
import { AudioLoader } from "./core/audio-loader.js";
import { AuthController } from "./auth/auth-controller.js?v=20260815.3";
import { GenerationController } from "./generation/generation-controller.js?v=20260817.2";
import { JobController } from "./jobs/job-controller.js";
import { JobDetailView } from "./jobs/job-detail-view.js";
import { JobListView } from "./jobs/job-list-view.js";
import { ProjectController } from "./projects/project-controller.js?v=20260818.1";
import { ScriptController } from "./scripts/script-controller.js?v=20260817.2";
import { ScriptDetailView } from "./scripts/script-detail-view.js";
import { ScriptListView } from "./scripts/script-list-view.js";
import { SystemController } from "./system/system-controller.js";
import { ShellController } from "./ui/shell-controller.js?v=20260817.1";
import { VoiceController } from "./voices/voice-controller.js";
import { VoiceDetailView } from "./voices/voice-detail-view.js";
import { VoiceListView } from "./voices/voice-list-view.js";

class VoiceLabApp {
  #state = new AppState();
  #api = new ApiClient();
  #audioLoader = new AudioLoader();
  #shell = new ShellController(this.#state);
  #system = new SystemController(this.#api, this.#shell);
  #auth = new AuthController(this.#api, "appShell", (user) => this.#boot(user));
  #projects;
  #jobs;
  #voices;
  #scripts;
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
      onVoicesChanged: () => {
        this.#generation?.renderOptions();
        this.#scripts?.render();
      },
    });
    this.#scripts = new ScriptController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      listView: new ScriptListView(this.#state),
      detailView: new ScriptDetailView(this.#state),
      onScriptsChanged: () => this.#generation?.renderOptions(),
    });
    this.#generation = new GenerationController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      jobs: this.#jobs,
      onManageScripts: () => this.#shell.showView("scripts"),
    });
    this.#candidates = new CandidateController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      jobs: this.#jobs,
    });
    this.#projects = new ProjectController({
      state: this.#state,
      api: this.#api,
      shell: this.#shell,
      onProjectChanged: (project) => this.#loadProject(project),
    });
    this.#jobs.setCandidateController(this.#candidates);
  }

  async start() {
    this.#bind();
    const user = await this.#auth.restore();
    if (user) await this.#boot(user);
    this.#shell.reveal();
  }

  #bind() {
    this.#auth.bind();
    this.#shell.bind((view) => {
      if (view === "library") this.#voices.ensureSelection();
      if (view === "scripts") this.#scripts.ensureSelection();
    });
    this.#projects.bind();
    this.#jobs.bind();
    this.#voices.bind();
    this.#scripts.bind();
    this.#generation.bind();
    this.#candidates.bind();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden || !this.#state.user) return;
      Promise.all([this.#jobs.onVisible(), this.#system.refreshHealth()]);
    });
  }

  async #boot(user) {
    this.#state.user = user;
    this.#shell.renderIdentity(user);
    try {
      const project = await this.#projects.load();
      if (!project) {
        this.#shell.showView("project");
        return;
      }
      await this.#loadProject(project);
      this.#shell.showView(this.#state.view);
      this.#system.refreshHealth();
      this.#jobs.schedule();
      this.#system.start();
    } catch (error) {
      this.#shell.toast(error.message, true);
      this.#shell.renderOffline();
    }
  }

  async #loadProject(project) {
    if (!project) {
      this.#state.voices = [];
      this.#state.scripts = [];
      this.#state.jobs = [];
      this.#generation.renderOptions();
      this.#voices.render();
      this.#scripts.render();
      this.#jobs.render();
      return;
    }
    const projectId = encodeURIComponent(project.id);
    const [config, voices, scripts, jobs] = await Promise.all([
      this.#api.get("/api/config"),
      this.#api.get(`/api/voices?project_id=${projectId}`),
      this.#api.get(`/api/scripts?project_id=${projectId}`),
      this.#api.get(`/api/jobs?limit=300&project_id=${projectId}`),
    ]);
    this.#state.config = config;
    this.#state.voices = voices.voices;
    this.#state.scripts = scripts.scripts;
    this.#state.jobs = jobs.jobs;
    this.#generation.renderOptions();
    this.#voices.render();
    this.#scripts.render();
    this.#jobs.render();
    await this.#jobs.selectInitial();
    if (this.#state.view === "library") await this.#voices.ensureSelection();
    if (this.#state.view === "scripts") await this.#scripts.ensureSelection();
  }
}

new VoiceLabApp().start();
