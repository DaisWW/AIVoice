import { $ } from "../core/dom.js";

export const GENERATION_KEYS = Object.freeze([
  "temperature",
  "speed_factor",
  "top_k",
  "top_p",
  "repetition_penalty",
]);

export class GenerationSettingsControls {
  #state;
  #inputs;
  #supported;

  constructor(state, inputs) {
    this.#state = state;
    this.#inputs = inputs;
    this.#supported = new Set(Object.keys(inputs));
  }

  bind() {
    Object.values(this.#inputs).forEach((id) => {
      $("#" + id)?.addEventListener("input", () => this.syncOutputs());
    });
  }

  configure() {
    (this.#state.config.generation_controls || []).forEach((control) => {
      const input = this.#input(control.key);
      if (!input) return;
      input.min = control.min;
      input.max = control.max;
      input.step = control.step;
      input.title = control.hint;
    });
  }

  set(settings) {
    Object.entries(this.#inputs).forEach(([key, id]) => {
      if (settings[key] !== undefined) $("#" + id).value = settings[key];
    });
    this.syncOutputs();
  }

  setSupported(keys) {
    this.#supported = new Set(keys || Object.keys(this.#inputs));
    Object.entries(this.#inputs).forEach(([key, id]) => {
      const input = $("#" + id);
      const container = input?.closest("label");
      if (container) container.hidden = !this.#supported.has(key);
    });
  }

  values() {
    return Object.fromEntries(
      Object.entries(this.#inputs)
        .filter(([key]) => this.#supported.has(key))
        .map(([key, id]) => [key, Number($("#" + id).value)]),
    );
  }

  syncOutputs() {
    Object.values(this.#inputs).forEach((id) => {
      const input = $("#" + id);
      const output = $("#" + id + "Value");
      if (input && output) output.textContent = input.value;
    });
  }

  #input(key) {
    const id = this.#inputs[key];
    return id ? $("#" + id) : null;
  }
}
