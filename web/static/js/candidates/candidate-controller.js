import { $, setButtonBusy } from "../core/dom.js";

export class CandidateController {
  #state;
  #api;
  #shell;
  #jobs;
  #job = null;
  #item = null;
  #source = null;

  constructor({ state, api, shell, jobs }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#jobs = jobs;
  }

  bind() {
    $("#candidateRegenerateForm").addEventListener("submit", (event) => this.#regenerate(event));
    ["candidateTemperature", "candidateSpeed", "candidateTopK", "candidateTopP", "candidatePenalty"].forEach((id) => {
      $("#" + id)?.addEventListener("input", () => this.#syncGenerationOutputs());
    });
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
    $("#candidateSeed").value = "";
    const defaults = this.#state.config.generation_defaults || {};
    $("#candidateTemperature").value = source?.generation_settings?.temperature ?? defaults.temperature ?? 0.8;
    $("#candidateSpeed").value = source?.generation_settings?.speed_factor ?? defaults.speed_factor ?? 1;
    $("#candidateTopK").value = source?.generation_settings?.top_k ?? defaults.top_k ?? 15;
    $("#candidateTopP").value = source?.generation_settings?.top_p ?? defaults.top_p ?? 0.9;
    $("#candidatePenalty").value = source?.generation_settings?.repetition_penalty ?? defaults.repetition_penalty ?? 1.25;
    $("#candidateSource").textContent = source?.name || "当前采用候选";
    this.#syncGenerationOutputs();
    $("#candidateRegenerateDialog").showModal();
    $("#candidateText").focus();
  }

  #syncGenerationOutputs() {
    const pairs = [
      ["candidateTemperature", "candidateTemperatureValue"],
      ["candidateSpeed", "candidateSpeedValue"],
      ["candidateTopK", "candidateTopKValue"],
      ["candidateTopP", "candidateTopPValue"],
      ["candidatePenalty", "candidatePenaltyValue"],
    ];
    pairs.forEach(([input, output]) => {
      const element = $("#" + input);
      const target = $("#" + output);
      if (element && target) target.textContent = element.value;
    });
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
        generation_settings: {
          temperature: Number($("#candidateTemperature").value),
          speed_factor: Number($("#candidateSpeed").value),
          top_k: Number($("#candidateTopK").value),
          top_p: Number($("#candidateTopP").value),
          repetition_penalty: Number($("#candidatePenalty").value),
        },
      };
      const path = `${this.#state.jobApiBase}/${encodeURIComponent(this.#job.id)}/items/${encodeURIComponent(this.#item.id)}/regenerate`;
      await this.#api.post(path, body);
      $("#candidateRegenerateDialog").close();
      this.#shell.toast("单句已加入 GPT 队列");
      await this.#jobs.refresh(true);
      this.#jobs.schedule(600);
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }
}
