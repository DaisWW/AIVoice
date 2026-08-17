import { $, renderSelect } from "../core/dom.js";
import { isVoiceUsable } from "../scripts/script-voice-utils.js";

export class ScriptSelectionControl {
  #state;

  constructor(state) {
    this.#state = state;
  }

  get value() {
    return $("#scriptSelect").value;
  }

  bind() {
    $("#scriptSelect").addEventListener("change", () => this.#renderSummary());
  }

  render() {
    const voices = new Map(this.#state.voices.map((voice) => [voice.id, voice]));
    const scripts = this.#state.scripts.map((script) => {
      const voice = voices.get(script.default_voice_id);
      return {
        ...script,
        disabled: !isVoiceUsable(voice),
        voice_name: voice?.name || "",
      };
    });
    const select = $("#scriptSelect");
    renderSelect(select, scripts, "选择台本", (script) => this.#label(script));
    if (scripts.find((script) => script.id === select.value)?.disabled) {
      select.value = "";
    }
    const first = scripts.find((script) => !script.disabled);
    if (!select.value && first) select.value = first.id;
    this.#renderSummary();
  }

  validationMessage() {
    if (!this.value) return "请选择台本";
    const script = this.#state.scripts.find((item) => item.id === this.value);
    return isVoiceUsable(this.#configuredVoice(script))
      ? ""
      : "请先到台本库配置声音";
  }

  #label(script) {
    if (!script.voice_name) return `${script.name} · 未配置声音`;
    if (script.disabled) return `${script.name} · ${script.voice_name} · 无启用录音`;
    return `${script.name} · ${script.voice_name} · ${script.item_count} 段`;
  }

  #renderSummary() {
    const script = this.#state.scripts.find((item) => item.id === this.value);
    const configuredVoice = this.#configuredVoice(script);
    const usable = isVoiceUsable(configuredVoice);
    $("#boundVoiceName").textContent = configuredVoice?.name || "尚未配置声音";
    $("#boundVoiceNote").textContent = usable
      ? `${configuredVoice.enabled_file_count} 条录音已启用 · 来自台本库配置`
      : configuredVoice
        ? "该声音库没有启用录音，请先到台本库重新配置"
        : "请先到台本库为台本选择声音";
    $("#scriptVoiceSummary").classList.toggle("missing", !usable);
  }

  #configuredVoice(script) {
    return this.#state.voices.find(
      (voice) => voice.id === script?.default_voice_id,
    );
  }
}
