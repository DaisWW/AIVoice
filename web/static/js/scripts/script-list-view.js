import { $, escapeHtml } from "../core/dom.js";
import { isVoiceUsable } from "./script-voice-utils.js";

export class ScriptListView {
  #state;
  #root;
  #searchQuery = "";

  constructor(state) {
    this.#state = state;
    this.#root = $("#scriptList");
  }

  setSearchQuery(query) {
    this.#searchQuery = String(query ?? "");
    this.render();
  }

  render() {
    const scripts = this.#filteredScripts();
    $("#scriptCount").textContent = this.#state.scripts.length;
    if (!scripts.length) {
      this.#root.innerHTML = this.#state.scripts.length && this.#searchQuery.trim()
        ? '<div class="list-empty">没有匹配的台本</div>'
        : '<div class="list-empty">暂无台本<br>上传后先配置声音，再进入生成。</div>';
      return;
    }
    const voices = new Map(this.#state.voices.map((voice) => [voice.id, voice]));
    this.#root.innerHTML = scripts
      .map((script) => this.#itemMarkup(script, voices.get(script.default_voice_id)))
      .join("");
  }

  #filteredScripts() {
    const query = this.#searchQuery.trim().toLowerCase();
    if (!query) return this.#state.scripts;
    return this.#state.scripts.filter((script) =>
      String(script.name ?? "").toLowerCase().includes(query),
    );
  }

  #itemMarkup(script, configuredVoice) {
    const active = script.id === this.#state.selectedScriptId;
    const usable = isVoiceUsable(configuredVoice);
    const voiceLabel = usable
      ? configuredVoice.name
      : configuredVoice
        ? `${configuredVoice.name} · 无启用录音`
        : "未配置声音";
    return `
      <button class="voice-list-button script-list-button${active ? " active" : ""}${usable ? "" : " unconfigured"}"
              type="button" data-script-id="${escapeHtml(script.id)}" aria-current="${String(active)}">
        <strong>${escapeHtml(script.name)}</strong>
        <span>${escapeHtml(voiceLabel)} · ${Number(script.item_count || 0)} 段</span>
      </button>
    `;
  }
}
