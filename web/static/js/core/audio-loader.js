import { $$ } from "./dom.js";

export class AudioLoader {
  #observer;
  #activeAudio = null;
  #handlePlay = (event) => {
    const audio = this.#audioTarget(event);
    if (!audio) return;
    if (this.#activeAudio && this.#activeAudio !== audio) {
      this.#activeAudio.pause();
    }
    this.#activeAudio = audio;
  };
  #handleStop = (event) => {
    const audio = this.#audioTarget(event);
    if (audio === this.#activeAudio) this.#activeAudio = null;
  };

  constructor() {
    this.#observer = "IntersectionObserver" in window
      ? new IntersectionObserver((entries) => this.#loadVisible(entries), {
        rootMargin: "280px 0px",
      })
      : null;
    document.addEventListener("play", this.#handlePlay, true);
    document.addEventListener("pause", this.#handleStop, true);
    document.addEventListener("ended", this.#handleStop, true);
  }

  prepare(root = document) {
    $$("audio[data-lazy-audio]", root).forEach((audio) => {
      audio.removeAttribute("data-lazy-audio");
      if (this.#observer) this.#observer.observe(audio);
      else audio.preload = "metadata";
    });
  }

  release(root) {
    if (this.#activeAudio && root.contains(this.#activeAudio)) {
      this.#activeAudio.pause();
      this.#activeAudio = null;
    }
    if (this.#observer) {
      $$("audio", root).forEach((audio) => this.#observer.unobserve(audio));
    }
  }

  #audioTarget(event) {
    const audio = event.target;
    return audio?.tagName === "AUDIO" ? audio : null;
  }

  #loadVisible(entries) {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const audio = entry.target;
      this.#observer?.unobserve(audio);
      audio.preload = "metadata";
      audio.load();
    });
  }
}
