import { $, setButtonBusy } from "../core/dom.js";
import { GenerationSettingsControls } from "../generation/generation-settings-controls.js";

const GENERATION_INPUTS = Object.freeze({
  temperature: "candidateTemperature",
  speed_factor: "candidateSpeed",
  top_k: "candidateTopK",
  top_p: "candidateTopP",
  repetition_penalty: "candidatePenalty",
});

export class CandidateController {
  #state;
  #api;
  #shell;
  #jobs;
  #controls;
  #job = null;
  #item = null;
  #source = null;

  constructor({ state, api, shell, jobs }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#jobs = jobs;
    this.#controls = new GenerationSettingsControls(state, GENERATION_INPUTS);
  }

  bind() {
    $("#candidateRegenerateForm").addEventListener("submit", (event) => this.#regenerate(event));
    $("#candidateLockSeed").addEventListener("change", () => this.#syncSeedLock());
    this.#controls.bind();
  }

  openRegenerate(job, item, source = null) {
    if (!job || !item) return;
    this.#job = job;
    this.#item = item;
    this.#source = source;
    $("#candidateText").value = source?.text || item.text || "";
    $("#candidatePronunciation").value = source?.pronunciation || item.pronunciation || "";
    $("#candidateDirection").value = source?.direction || item.direction || "auto";
    $("#candidateName").value = `重做 ${((item.candidates || []).length || 0) + 1}`;
    const hasSourceSeed = source?.seed !== null && source?.seed !== undefined;
    $("#candidateLockSeed").checked = hasSourceSeed;
    $("#candidateSeed").value = hasSourceSeed ? source.seed : "";
    this.#syncSeedLock(false);
    this.#controls.configure();
    const model = this.#state.model(job.model_id);
    this.#controls.setSupported(model?.generation_parameters);
    this.#controls.set({
      ...this.#modelDefaults(job.model_id),
      ...(source?.generation_settings || {}),
    });
    $("#candidateSource").textContent = source?.name || "当前采用候选";
    $("#candidateRegenerateDialog").showModal();
    $("#candidateText").focus();
  }

  #modelDefaults(modelId) {
    const model = this.#state.model(modelId);
    return {
      ...(this.#state.config.generation_defaults || {}),
      ...(model?.generation_defaults || {}),
    };
  }

  #syncSeedLock(clearUnlocked = true) {
    const locked = $("#candidateLockSeed").checked;
    const input = $("#candidateSeed");
    input.disabled = locked;
    if (locked && this.#source?.seed !== undefined) input.value = this.#source.seed;
    else if (clearUnlocked) input.value = "";
  }

  async #regenerate(event) {
    event.preventDefault();
    if (!this.#job || !this.#item) return;
    const button = $("button[type='submit']", event.currentTarget);
    setButtonBusy(button, true);
    try {
      const seed = $("#candidateSeed").value.trim();
      const body = {
        name: $("#candidateName").value.trim(),
        text: $("#candidateText").value.trim(),
        pronunciation: $("#candidatePronunciation").value.trim(),
        direction: $("#candidateDirection").value,
        source_candidate_id: this.#source?.id || null,
        seed: seed ? Number(seed) : null,
        generation_settings: this.#controls.values(),
      };
      const path = `${this.#state.jobApiBase}/${encodeURIComponent(this.#job.id)}/items/${encodeURIComponent(this.#item.id)}/regenerate`;
      await this.#api.post(path, body);
      $("#candidateRegenerateDialog").close();
      this.#shell.toast("单句已加入生成队列");
      await this.#jobs.refresh(true);
      this.#jobs.schedule(600);
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }
}
