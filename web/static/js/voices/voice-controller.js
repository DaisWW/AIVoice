import { $, escapeHtml, setButtonBusy } from "../core/dom.js";
import { formatBytes } from "../core/formatters.js";

const SUPPORTED_AUDIO_EXTENSIONS = new Set([
  ".wav",
  ".mp3",
  ".m4a",
  ".aac",
  ".flac",
  ".ogg",
  ".opus",
]);

const AUDIO_EXTENSION_BY_MIME = {
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/wave": ".wav",
  "audio/mpeg": ".mp3",
  "audio/mp3": ".mp3",
  "audio/mp4": ".m4a",
  "audio/x-m4a": ".m4a",
  "audio/aac": ".aac",
  "audio/flac": ".flac",
  "audio/ogg": ".ogg",
  "audio/opus": ".opus",
};

export class VoiceController {
  #state;
  #api;
  #shell;
  #listView;
  #detailView;
  #onVoicesChanged;
  #request = null;
  #selectedVoiceFiles = [];

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
    $("#voiceSearch").addEventListener("input", (event) => {
      this.#listView.setSearchQuery(event.currentTarget.value);
    });
    $("#voiceDetail").addEventListener("submit", (event) => this.#handleDetailSubmit(event));
    $("#voiceDetail").addEventListener("change", (event) => this.#handleDetailChange(event));
    $("#createVoice").addEventListener("click", () => this.#openCreate());
    $("#voiceCreateForm").addEventListener("submit", (event) => this.#create(event));
    $("#voiceFiles").addEventListener("change", (event) => {
      this.#addVoiceFiles(event.target.files);
      event.target.value = "";
    });
    $("#voiceFolder").addEventListener("change", (event) => {
      this.#addVoiceFiles(event.target.files);
      event.target.value = "";
    });
    $("#voiceSelectedFiles").addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-voice-file]");
      if (button) this.#removeVoiceFile(Number(button.dataset.removeVoiceFile));
    });
    $("#voicePasteButton").addEventListener("click", () => this.#pasteVoiceFiles());
    const dropZone = $("#voiceDropZone");
    dropZone.addEventListener("click", (event) => {
      if (!event.target.closest("label, button, input")) $("#voiceFiles").click();
    });
    dropZone.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      $("#voiceFiles").click();
    });
    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("drag-over");
      });
    });
    dropZone.addEventListener("dragleave", (event) => {
      if (!event.relatedTarget || !dropZone.contains(event.relatedTarget)) {
        dropZone.classList.remove("drag-over");
      }
    });
    dropZone.addEventListener("drop", (event) => this.#handleVoiceDrop(event));
    $("#voiceDialog").addEventListener("paste", (event) => this.#handleVoicePaste(event));
    $("#voiceDialog").addEventListener("close", () => this.#resetCreateForm());
  }

  render() {
    this.#listView.render();
  }

  async refresh() {
    const { voices } = await this.#api.get(
      `/api/voices?project_id=${encodeURIComponent(this.#state.projectId || "")}`,
    );
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
    if (event.target.matches("[data-file-reference-id]")) {
      this.#changeReferenceText(event.target);
    }
  }

  #openCreate() {
    this.#resetCreateForm();
    $("#voiceDialog").showModal();
    $("#voiceName").focus();
  }

  #addVoiceFiles(files) {
    const accepted = [];
    const rejected = [];
    const seen = new Set(this.#selectedVoiceFiles.map((file) => this.#fileKey(file)));
    const batchId = Date.now();
    for (const [index, candidate] of Array.from(files || []).entries()) {
      const file = this.#prepareVoiceFile(candidate, batchId, index);
      if (!file) continue;
      if (!this.#isSupportedAudio(file)) {
        rejected.push(file.name || "未命名文件");
        continue;
      }
      const key = this.#fileKey(file);
      if (seen.has(key)) continue;
      seen.add(key);
      accepted.push(file);
    }
    this.#selectedVoiceFiles.push(...accepted);
    this.#syncVoiceFileInput();
    this.#renderVoiceFiles();
    if (rejected.length) {
      const preview = rejected.slice(0, 2).join("、");
      const suffix = rejected.length > 2 ? ` 等 ${rejected.length} 个文件` : "";
      this.#setVoiceFileError(`已跳过不支持的文件：${preview}${suffix}`);
    } else if (accepted.length) {
      this.#setVoiceFileError("");
    }
  }

  #prepareVoiceFile(file, batchId = Date.now(), index = 0) {
    if (!file || typeof file !== "object") return null;
    const name = String(file.name || "");
    if (name && /\.[^./\\]+$/.test(name)) return file;
    const mime = String(file.type || "").split(";", 1)[0].toLowerCase();
    const extension = AUDIO_EXTENSION_BY_MIME[mime];
    if (!extension || typeof File === "undefined") return file;
    try {
      return new File([file], `剪贴板音频-${batchId}-${index + 1}${extension}`, {
        type: mime,
        lastModified: Number(file.lastModified) || batchId,
      });
    } catch (_) {
      return file;
    }
  }

  #isSupportedAudio(file) {
    const name = String(file.name || "");
    const extension = name.includes(".")
      ? name.slice(name.lastIndexOf(".")).toLowerCase()
      : "";
    return SUPPORTED_AUDIO_EXTENSIONS.has(extension);
  }

  #fileKey(file) {
    return [
      file.webkitRelativePath || file.name || "",
      file.size || 0,
      file.lastModified || 0,
      file.type || "",
    ].join("\u0000");
  }

  #syncVoiceFileInput() {
    const input = $("#voiceFiles");
    if (typeof DataTransfer === "undefined") return;
    try {
      const transfer = new DataTransfer();
      this.#selectedVoiceFiles.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    } catch (_) {
      // FormData is assembled from #selectedVoiceFiles, so this is only a browser UX fallback.
    }
  }

  #renderVoiceFiles() {
    const count = this.#selectedVoiceFiles.length;
    $("#voiceFileName").textContent = count ? `已选择 ${count} 条录音` : "尚未选择录音";
    const list = $("#voiceSelectedFiles");
    if (!count) {
      list.innerHTML = '<li class="voice-selected-empty">还没有添加音频</li>';
      return;
    }
    list.innerHTML = this.#selectedVoiceFiles.map((file, index) => {
      const name = file.webkitRelativePath || file.name || "未命名音频";
      const type = file.type || "音频文件";
      return `
        <li class="voice-selected-file">
          <span class="voice-selected-file-icon" aria-hidden="true">♪</span>
          <span class="voice-selected-file-info">
            <strong title="${escapeHtml(name)}">${escapeHtml(name)}</strong>
            <small>${escapeHtml(formatBytes(file.size))} · ${escapeHtml(type)}</small>
          </span>
          <button class="voice-file-remove" type="button" data-remove-voice-file="${index}" title="移除" aria-label="移除 ${escapeHtml(name)}">×</button>
        </li>
      `;
    }).join("");
  }

  #removeVoiceFile(index) {
    if (!Number.isInteger(index) || index < 0 || index >= this.#selectedVoiceFiles.length) return;
    this.#selectedVoiceFiles.splice(index, 1);
    this.#syncVoiceFileInput();
    this.#renderVoiceFiles();
    this.#setVoiceFileError("");
  }

  #setVoiceFileError(message) {
    const error = $("#voiceFileError");
    error.textContent = message;
    error.hidden = !message;
  }

  async #handleVoiceDrop(event) {
    event.preventDefault();
    const dropZone = $("#voiceDropZone");
    dropZone.classList.remove("drag-over");
    dropZone.classList.add("is-reading");
    try {
      this.#addVoiceFiles(await this.#filesFromDrop(event.dataTransfer));
    } catch (_) {
      this.#shell.toast("无法读取拖入的文件夹，请改用“选择文件夹”", true);
    } finally {
      dropZone.classList.remove("is-reading");
    }
  }

  async #filesFromDrop(dataTransfer) {
    if (!dataTransfer) return [];
    const direct = Array.from(dataTransfer.files || []);
    const entries = Array.from(dataTransfer.items || [])
      .map((item) => item.webkitGetAsEntry?.())
      .filter(Boolean);
    if (!entries.length) return direct;
    const discovered = [];
    for (const entry of entries) await this.#readDropEntry(entry, discovered);
    return [...direct, ...discovered];
  }

  async #readDropEntry(entry, files) {
    if (entry.isFile) {
      const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
      if (file) files.push(file);
      return;
    }
    if (!entry.isDirectory) return;
    const reader = entry.createReader();
    while (true) {
      const batch = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
      if (!batch.length) break;
      for (const child of batch) await this.#readDropEntry(child, files);
    }
  }

  #handleVoicePaste(event) {
    const clipboard = event.clipboardData;
    const direct = Array.from(clipboard?.files || []);
    const items = Array.from(clipboard?.items || [])
      .filter((item) => item.kind === "file" && item.type?.toLowerCase().startsWith("audio/"))
      .map((item) => item.getAsFile?.())
      .filter(Boolean);
    const files = (direct.length ? direct : items).filter((file) => this.#looksLikeAudio(file));
    if (!files.length) return;
    event.preventDefault();
    this.#addVoiceFiles(files);
  }

  #looksLikeAudio(file) {
    const name = String(file?.name || "");
    const extension = name.includes(".")
      ? name.slice(name.lastIndexOf(".")).toLowerCase()
      : "";
    return SUPPORTED_AUDIO_EXTENSIONS.has(extension)
      || String(file?.type || "").toLowerCase().startsWith("audio/");
  }

  async #pasteVoiceFiles() {
    if (!navigator.clipboard?.read) {
      this.#shell.toast("浏览器未开放剪贴板读取，请在弹窗中按 Ctrl/Cmd+V 粘贴音频", true);
      return;
    }
    const button = $("#voicePasteButton");
    setButtonBusy(button, true);
    try {
      const clipboardItems = await navigator.clipboard.read();
      const files = [];
      for (const item of clipboardItems) {
        const type = item.types.find((value) => value.toLowerCase().startsWith("audio/"));
        if (type) files.push(await item.getType(type));
      }
      if (!files.length) {
        this.#shell.toast("剪贴板中没有可用的音频文件", true);
        return;
      }
      this.#addVoiceFiles(files);
    } catch (_) {
      this.#shell.toast("无法读取剪贴板，请在弹窗中按 Ctrl/Cmd+V 粘贴音频", true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  #resetCreateForm() {
    const form = $("#voiceCreateForm");
    form.reset();
    this.#selectedVoiceFiles = [];
    this.#setVoiceFileError("");
    this.#renderVoiceFiles();
    $("#voiceDropZone").classList.remove("drag-over", "is-reading");
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

  async #changeReferenceText(input) {
    const fileId = input.dataset.fileReferenceId;
    const previous = this.#state.voiceDetail?.files.find((item) => item.id === fileId)
      ?.reference_text || "";
    input.disabled = true;
    try {
      const { voice } = await this.#api.patch(
        `/api/voices/${encodeURIComponent(this.#state.selectedVoiceId)}/files/${encodeURIComponent(fileId)}`,
        { reference_text: input.value },
      );
      this.#applyFileUpdate(voice, fileId);
      this.#shell.toast("参考文本已更新");
    } catch (error) {
      input.value = previous;
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
    if (!this.#selectedVoiceFiles.length) {
      this.#addVoiceFiles([
        ...Array.from($("#voiceFiles").files || []),
        ...Array.from($("#voiceFolder").files || []),
      ]);
    }
    if (!this.#selectedVoiceFiles.length) {
      this.#setVoiceFileError("请先添加至少一条音频文件，再创建声音库");
      $("#voiceDropZone").focus();
      return;
    }
    const button = $("#submitVoice");
    setButtonBusy(button, true);
    button.textContent = "正在创建";
    try {
      const { voice } = await this.#api.postForm(
        "/api/voices",
        this.#formData(event.target),
      );
      this.#resetCreateForm();
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

  #formData(form) {
    const data = new FormData(form);
    data.delete("files");
    this.#selectedVoiceFiles.forEach((file) => {
      data.append("files", file, file.name || "voice-source.wav");
    });
    data.set("project_id", this.#state.projectId || "");
    return data;
  }
}
