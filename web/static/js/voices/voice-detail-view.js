import { $, $$, escapeHtml, setPanelLoading } from "../core/dom.js";
import { formatBytes, formatDate } from "../core/formatters.js";
import { voiceOwnerLabel } from "./voice-utils.js";

export class VoiceDetailView {
  #state;
  #audioLoader;
  #root;

  constructor(state, audioLoader) {
    this.#state = state;
    this.#audioLoader = audioLoader;
    this.#root = $("#voiceDetail");
  }

  get voiceId() {
    return this.#root.dataset.voiceId || null;
  }

  hasVoice(voiceId) {
    return this.voiceId === voiceId && Boolean($(".voice-detail-content", this.#root));
  }

  setLoading(loading) {
    setPanelLoading(this.#root, loading);
  }

  showInitialLoading() {
    if ($(".voice-detail-content", this.#root)) return;
    this.#root.innerHTML =
      '<div class="stage-empty compact"><span class="empty-number">··</span><h2>读取声音库</h2></div>';
  }

  clear() {
    this.#audioLoader.release(this.#root);
    delete this.#root.dataset.voiceId;
    this.#root.innerHTML =
      '<div class="stage-empty compact"><span class="empty-number">音频</span><h2>选择一个声音库</h2></div>';
  }

  render(voice) {
    const editable = Boolean(voice.can_edit);
    const files = voice.files.length
      ? voice.files.map((file) => this.#fileMarkup(file, editable)).join("")
      : '<div class="list-empty">暂无录音</div>';
    this.#audioLoader.release(this.#root);
    this.#root.innerHTML = this.#detailMarkup(voice, editable, files);
    this.#root.dataset.voiceId = voice.id;
    this.#audioLoader.prepare(this.#root);
  }

  patchFile(voice, fileId) {
    const input = $$("[data-file-id]", this.#root).find(
      (item) => item.dataset.fileId === fileId,
    );
    const file = voice.files.find((item) => item.id === fileId);
    if (!file) return;
    if (input) {
      input.checked = Boolean(file.enabled);
      input.closest(".switch").title = file.enabled ? "已启用" : "已停用";
    }
    const emotion = $$("[data-file-emotion-id]", this.#root).find(
      (item) => item.dataset.fileEmotionId === fileId,
    );
    if (emotion) emotion.value = file.emotion_tag;
    const referenceText = $$("[data-file-reference-id]", this.#root).find(
      (item) => item.dataset.fileReferenceId === fileId,
    );
    if (referenceText) referenceText.value = file.reference_text || "";
    const count = $(".voice-files-toolbar p", this.#root);
    if (count) count.textContent = `${voice.enabled_file_count}/${voice.file_count} 条启用`;
  }

  #detailMarkup(voice, editable, files) {
    return `
      <div class="voice-detail-content">
        <header class="voice-detail-header">
          <div><span class="section-kicker">VOICE SOURCE</span><h2>${escapeHtml(voice.name)}</h2></div>
          <span class="owner-badge">${escapeHtml(voiceOwnerLabel(this.#state, voice))}</span>
        </header>
        ${this.#formMarkup(voice, editable)}
        <section class="voice-files-section">
          ${this.#toolbarMarkup(voice, editable)}
          <div class="voice-files-list">${files}</div>
        </section>
      </div>
    `;
  }

  #formMarkup(voice, editable) {
    const disabled = editable ? "" : "disabled";
    return `
      <form id="voiceEditForm" class="voice-form">
        <div><label class="field-label" for="voiceEditName">名称</label><input id="voiceEditName" name="name" type="text" maxlength="80" value="${escapeHtml(voice.name)}" ${disabled} required></div>
        <div><label class="field-label">声音编号</label><input type="text" value="${escapeHtml(voice.id)}" disabled></div>
        <div class="wide"><label class="field-label" for="voiceEditNotes">备注</label><textarea id="voiceEditNotes" name="notes" rows="3" maxlength="500" ${disabled}>${escapeHtml(voice.notes)}</textarea></div>
        ${editable ? '<div class="voice-form-actions"><button class="primary-button" type="submit">保存修改</button></div>' : ""}
      </form>
    `;
  }

  #toolbarMarkup(voice, editable) {
    const append = editable ? `
      <form id="appendVoiceForm" class="append-form">
        <label class="upload-field compact-upload" for="appendVoiceFiles"><span id="appendFileName">选择录音</span></label>
        <input id="appendVoiceFiles" name="files" type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus" multiple required>
        <button class="secondary-button" type="submit">追加录音</button>
      </form>
    ` : "";
    return `
      <div class="voice-files-toolbar">
        <div><h3>原始录音</h3><p>${voice.enabled_file_count}/${voice.file_count} 条启用</p></div>
        ${append}
      </div>
    `;
  }

  #fileMarkup(file, editable) {
    const quality = file.quality || {};
    const issues = quality.issues || [];
    const emotionOptions = [
      ["neutral", "自然"],
      ["calm", "平静"],
      ["angry", "愤怒"],
      ["whisper", "耳语"],
      ["aged", "衰老"],
      ["sigh", "叹息"],
    ];
    return `
      <div class="voice-file">
        <div class="voice-file-name">
          <strong title="${escapeHtml(file.original_name)}">${escapeHtml(file.original_name)}</strong>
          <span>${escapeHtml(formatBytes(file.size_bytes))} · ${escapeHtml(formatDate(file.created_at))}</span>
        </div>
        <audio controls preload="none" data-lazy-audio src="${escapeHtml(file.audio_url)}"></audio>
        <div class="voice-file-meta">
          <span class="quality-badge ${escapeHtml(quality.grade || "unknown")}">质量 ${quality.score ?? "--"}</span>
          <span>${quality.snr_db != null ? `SNR ${quality.snr_db} dB` : "未检测"}</span>
          ${issues.length ? `<span title="${escapeHtml(issues.map((issue) => issue.message).join("；"))}">有 ${issues.length} 项提示</span>` : "<span>录音状态良好</span>"}
        </div>
        ${editable ? `<label class="emotion-select"><span>参考语气</span><select data-file-emotion-id="${escapeHtml(file.id)}">${emotionOptions.map(([id, label]) => `<option value="${id}" ${file.emotion_tag === id ? "selected" : ""}>${label}</option>`).join("")}</select></label>` : `<span class="emotion-label">参考语气：${escapeHtml(emotionOptions.find(([id]) => id === file.emotion_tag)?.[1] || "自然")}</span>`}
        <label class="switch" title="${file.enabled ? "已启用" : "已停用"}">
          <input type="checkbox" data-file-id="${escapeHtml(file.id)}" ${file.enabled ? "checked" : ""} ${editable ? "" : "disabled"}>
          <span></span>
        </label>
        ${editable ? `
          <label class="reference-text-field">
            <span>参考文本 <small>必须与录音逐字一致；虫语或纯拟声无法可靠转写时请留空</small></span>
            <textarea data-file-reference-id="${escapeHtml(file.id)}" rows="2" maxlength="2000" placeholder="仅填写实际发音，不要按中文猜写">${escapeHtml(file.reference_text || "")}</textarea>
          </label>
        ` : file.reference_text ? `<div class="reference-text-readonly"><span>参考文本</span><p>${escapeHtml(file.reference_text)}</p></div>` : ""}
      </div>
    `;
  }
}
