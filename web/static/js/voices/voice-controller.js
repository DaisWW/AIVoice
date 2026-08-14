import { $, setButtonBusy } from "../core/dom.js";

export class VoiceController {
  #state;
  #api;
  #shell;
  #listView;
  #detailView;
  #onVoicesChanged;
  #request = null;

  constructor({ state, api, shell, listView, detailView, onVoicesChanged }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#listView = listView;
    this.#detailView = detailView;
    this.#onVoicesChanged = onVoicesChanged;
  }

  bind() {
    $("#voiceList").addEventListener("click", (event) => this.#handleListClick(event));
    $("#voiceDetail").addEventListener("submit", (event) => this.#handleDetailSubmit(event));
    $("#voiceDetail").addEventListener("change", (event) => this.#handleDetailChange(event));
    $("#createVoice").addEventListener("click", () => this.#openCreate());
    $("#voiceCreateForm").addEventListener("submit", (event) => this.#create(event));
    $("#voiceFiles").addEventListener("change", (event) => {
      const count = event.target.files.length;
      $("#voiceFileName").textContent = count ? `已选择 ${count} 条录音` : "选择真人录音";
    });
  }

  render() {
    this.#listView.render();
  }

  async refresh() {
    const { voices } = await this.#api.get("/api/voices");
    this.#state.voices = voices;
    this.#listView.render();
    this.#onVoicesChanged();
  }

  async ensureSelection() {
    if (!this.#state.voices.length) {
      this.#detailView.clear();
      return;
    }
    const selected = this.#state.voices.find(
      (voice) => voice.id === this.#state.selectedVoiceId,
    );
    await this.select((selected || this.#state.voices[0]).id);
  }

  async select(voiceId) {
    const previousVoiceId = this.#state.voiceDetail?.id || null;
    this.#state.selectVoice(voiceId);
    this.#listView.render();
    if (previousVoiceId === voiceId && this.#detailView.hasVoice(voiceId)) {
      this.#cancelSelection();
      this.#detailView.setLoading(false);
      return;
    }
    await this.#loadSelection(voiceId, previousVoiceId);
  }

  #handleListClick(event) {
    const button = event.target.closest("[data-voice-id]");
    if (button) this.select(button.dataset.voiceId);
  }

  #handleDetailSubmit(event) {
    event.preventDefault();
    if (event.target.id === "voiceEditForm") this.#edit(event.target);
    if (event.target.id === "appendVoiceForm") this.#append(event.target);
  }

  #handleDetailChange(event) {
    if (event.target.id === "appendVoiceFiles") {
      const count = event.target.files.length;
      $("#appendFileName").textContent = count ? `已选 ${count} 条录音` : "选择录音";
    }
    if (event.target.matches("[data-file-id]")) this.#toggleFile(event.target);
    if (event.target.matches("[data-file-emotion-id]")) this.#changeEmotion(event.target);
  }

  #openCreate() {
    $("#voiceDialog").showModal();
    $("#voiceName").focus();
  }

  async #loadSelection(voiceId, previousVoiceId) {
    this.#cancelSelection();
    const controller = new AbortController();
    this.#request = controller;
    this.#detailView.setLoading(true);
    this.#detailView.showInitialLoading();
    try {
      const { voice } = await this.#api.get(
        `/api/voices/${encodeURIComponent(voiceId)}`,
        { signal: controller.signal },
      );
      if (this.#state.selectedVoiceId === voiceId) this.#sync(voice);
    } catch (error) {
      this.#handleSelectionError(error, voiceId, previousVoiceId);
    } finally {
      this.#finishSelection(controller, voiceId, previousVoiceId);
    }
  }

  #handleSelectionError(error, voiceId, previousVoiceId) {
    if (error.name === "AbortError") return;
    if (previousVoiceId && this.#state.selectedVoiceId === voiceId) {
      this.#state.selectVoice(previousVoiceId);
      this.#listView.render();
    }
    this.#shell.toast(error.message, true);
  }

  #finishSelection(controller, voiceId, previousVoiceId) {
    if (this.#request !== controller) return;
    this.#request = null;
    if (
      this.#state.selectedVoiceId === voiceId
      || this.#state.selectedVoiceId === previousVoiceId
    ) {
      this.#detailView.setLoading(false);
    }
  }

  #cancelSelection() {
    this.#request?.abort();
  }

  #sync(voice) {
    this.#storeVoice(voice);
    this.#state.selectVoice(voice.id);
    this.#state.voiceDetail = voice;
    this.#listView.render();
    this.#detailView.render(voice);
    this.#onVoicesChanged();
  }

  #storeVoice(voice) {
    const index = this.#state.voices.findIndex((item) => item.id === voice.id);
    if (index >= 0) this.#state.voices[index] = { ...this.#state.voices[index], ...voice };
    else this.#state.voices.unshift(voice);
  }

  async #edit(form) {
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      const { voice } = await this.#api.patch(
        `/api/voices/${encodeURIComponent(this.#state.selectedVoiceId)}`,
        { name: $("#voiceEditName").value, notes: $("#voiceEditNotes").value },
      );
      this.#sync(voice);
      this.#shell.toast("声音库已更新");
    } catch (error) {
      this.#shell.toast(error.message, true);
      setButtonBusy(button, false);
    }
  }

  async #append(form) {
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      const { voice } = await this.#api.postForm(
        `/api/voices/${encodeURIComponent(this.#state.selectedVoiceId)}/files`,
        new FormData(form),
      );
      this.#sync(voice);
      this.#shell.toast("录音已追加");
    } catch (error) {
      this.#shell.toast(error.message, true);
      setButtonBusy(button, false);
    }
  }

  async #toggleFile(input) {
    const enabled = input.checked;
    const switchElement = input.closest(".switch");
    input.disabled = true;
    switchElement.classList.add("pending");
    try {
      const { voice } = await this.#api.patch(
        `/api/voices/${encodeURIComponent(this.#state.selectedVoiceId)}/files/${encodeURIComponent(input.dataset.fileId)}`,
        { enabled },
      );
      this.#applyFileUpdate(voice, input.dataset.fileId);
      const file = voice.files.find((item) => item.id === input.dataset.fileId);
      this.#shell.toast(file?.enabled ? "录音已启用" : "录音已停用");
    } catch (error) {
      input.checked = !enabled;
      this.#shell.toast(error.message, true);
    } finally {
      input.disabled = false;
      switchElement.classList.remove("pending");
    }
  }

  async #changeEmotion(input) {
    const fileId = input.dataset.fileEmotionId;
    const previous = this.#state.voiceDetail?.files.find((item) => item.id === fileId)
      ?.emotion_tag;
    input.disabled = true;
    try {
      const { voice } = await this.#api.patch(
        `/api/voices/${encodeURIComponent(this.#state.selectedVoiceId)}/files/${encodeURIComponent(input.dataset.fileEmotionId)}`,
        { emotion_tag: input.value },
      );
      this.#applyFileUpdate(voice, fileId);
      this.#shell.toast("参考语气已更新");
    } catch (error) {
      if (previous) input.value = previous;
      this.#shell.toast(error.message, true);
    } finally {
      input.disabled = false;
    }
  }

  #applyFileUpdate(voice, fileId) {
    this.#storeVoice(voice);
    this.#state.voiceDetail = voice;
    this.#listView.render();
    this.#detailView.patchFile(voice, fileId);
    this.#onVoicesChanged();
  }

  async #create(event) {
    event.preventDefault();
    const button = $("#submitVoice");
    setButtonBusy(button, true);
    button.textContent = "正在创建";
    try {
      const { voice } = await this.#api.postForm(
        "/api/voices",
        new FormData(event.target),
      );
      event.target.reset();
      $("#voiceFileName").textContent = "选择真人录音";
      $("#voiceDialog").close();
      await this.refresh();
      await this.select(voice.id);
      this.#shell.toast("声音库已创建");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
      button.textContent = "创建";
    }
  }
}
