import { $, $$ } from "../core/dom.js";
import { voiceOwnerLabel } from "./voice-utils.js";

export class VoiceListView {
  #state;
  #root;

  constructor(state) {
    this.#state = state;
    this.#root = $("#voiceList");
  }

  render() {
    $("#voiceCount").textContent = this.#state.voices.length;
    if (!this.#state.voices.length) {
      this.#renderEmpty();
      return;
    }
    $(".list-empty", this.#root)?.remove();
    const existing = new Map(
      $$(".voice-list-button", this.#root).map(
        (button) => [button.dataset.voiceId, button],
      ),
    );
    this.#state.voices.forEach((voice, index) => {
      this.#placeVoice(voice, index, existing);
    });
    existing.forEach((button) => button.remove());
  }

  #renderEmpty() {
    if (!$(".list-empty", this.#root) || $(".voice-list-button", this.#root)) {
      this.#root.innerHTML = '<div class="list-empty">暂无声音库</div>';
    }
  }

  #placeVoice(voice, index, existing) {
    const button = existing.get(voice.id) || this.#createButton();
    this.#updateButton(button, voice);
    const current = this.#root.children[index];
    if (current !== button) this.#root.insertBefore(button, current || null);
    existing.delete(voice.id);
  }

  #createButton() {
    const button = document.createElement("button");
    button.className = "voice-list-button";
    button.type = "button";
    button.innerHTML = "<strong></strong><span></span>";
    return button;
  }

  #updateButton(button, voice) {
    const renderKey = this.#renderKey(voice);
    if (button.dataset.renderKey === renderKey) return;
    const active = voice.id === this.#state.selectedVoiceId;
    button.dataset.voiceId = voice.id;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", String(active));
    $("strong", button).textContent = voice.name;
    $("span", button).textContent =
      `${voiceOwnerLabel(this.#state, voice)} · ${voice.enabled_file_count || 0}/${voice.file_count || 0} 条启用`;
    button.dataset.renderKey = renderKey;
  }

  #renderKey(voice) {
    return JSON.stringify([
      voice.name,
      voiceOwnerLabel(this.#state, voice),
      voice.enabled_file_count,
      voice.file_count,
      voice.id === this.#state.selectedVoiceId,
    ]);
  }
}
