import { $, $$, escapeHtml, multiline, setPanelLoading } from "../core/dom.js";
import {
  isActiveJob,
  jobHint,
  normalizedStatus,
  safeProgress,
  statusText,
} from "../core/formatters.js";
import { JobAudioRowView } from "./job-audio-row-view.js";

export class JobDetailView {
  #state;
  #audioLoader;
  #root;
  #job = null;
  #rows = new JobAudioRowView();

  constructor(state, audioLoader) {
    this.#state = state;
    this.#audioLoader = audioLoader;
    this.#root = $("#jobDetail");
  }

  get jobId() {
    return this.#root.dataset.jobId || null;
  }

  hasJob(jobId) {
    return this.jobId === jobId && Boolean($(".detail-content", this.#root));
  }

  setLoading(loading) {
    setPanelLoading(this.#root, loading);
  }

  clear() {
    this.#audioLoader.release(this.#root);
    this.#job = null;
    this.#rows.clear();
    delete this.#root.dataset.jobId;
    this.#root.innerHTML =
      '<div class="stage-empty"><span class="empty-number">00</span><h2>选择一条生成记录</h2></div>';
  }

  showInitialLoading() {
    if ($(".detail-content", this.#root)) return;
    this.#root.innerHTML =
      '<div class="stage-empty"><span class="empty-number">··</span><h2>读取任务</h2></div>';
  }

  render(job) {
    if (this.#job?.id !== job.id) this.#rows.clear();
    this.#job = job;
    if (!this.#patch(job)) this.#renderFull(job);
  }

  selectCandidate(itemId, candidateId) {
    if (!this.#job) return;
    this.#rows.selectCandidate(itemId, candidateId);
    this.#replaceRow(itemId);
  }

  #patch(job) {
    const rows = $$(".audio-item", this.#root);
    if (!this.#canPatch(job, rows)) return false;
    this.#patchHeader(job);
    this.#patchSummary(job);
    this.#patchProgress(job);
    this.#patchError(job.error);
    this.#patchItems(this.#rows.models(job));
    return true;
  }

  #canPatch(job, rows) {
    if (!this.hasJob(job.id)) return false;
    return rows.length === (job.items || []).length
      && rows.every((row, index) => row.dataset.itemId === String(job.items[index].id));
  }

  #patchHeader(job) {
    this.#updateStatus($("[data-job-status]", this.#root), job.status);
    const title = $("[data-job-title]", this.#root);
    title.textContent = job.name;
    title.title = job.name;
    this.#updateDownload(job);
    this.#updateFormalExport(job);
  }

  #patchSummary(job) {
    $("[data-overview-value='voice']", this.#root).textContent = job.voice_name;
    $("[data-overview-value='model']", this.#root).textContent = this.#modelLabel(job.model_id);
    $("[data-overview-value='owner']", this.#root).textContent =
      this.#state.isAdmin ? job.client_id : job.id;
    const accepted = $("[data-accepted-count]", this.#root);
    if (accepted) accepted.textContent = `${job.accepted_items || 0}/${job.total_items} 已采用`;
  }

  #patchProgress(job) {
    $("[data-job-progress]", this.#root).style.width = `${safeProgress(job)}%`;
    $("[data-progress-count]", this.#root).textContent =
      `${job.completed_items}/${job.total_items} 段`;
    $("[data-progress-hint]", this.#root).textContent = jobHint(job);
  }

  #patchError(message) {
    let element = $("[data-detail-error]", this.#root);
    if (!message) {
      element?.remove();
      return;
    }
    if (!element) {
      element = document.createElement("pre");
      element.className = "detail-error";
      element.dataset.detailError = "";
      $(".script-heading", this.#root).before(element);
    }
    element.textContent = message;
  }

  #patchItems(models) {
    const rows = new Map(
      $$(".audio-item", this.#root).map((row) => [row.dataset.itemId, row]),
    );
    models.forEach((model) => {
      const row = rows.get(String(model.id));
      if (!row) return;
      if (row.dataset.rowKey !== this.#rows.rowKey(model)) {
        this.#replaceRow(model.id);
        return;
      }
      this.#patchTranscript(row, model);
      this.#patchAudio($(".audio-control", row), model);
    });
  }

  #patchTranscript(row, item) {
    const textKey = JSON.stringify([item.displayText, item.displayPronunciation]);
    if (row.dataset.textKey === textKey) return;
    $(".item-text", row).innerHTML = multiline(item.displayText);
    $(".pronunciation", row).innerHTML = multiline(item.displayPronunciation);
    row.dataset.textKey = textKey;
  }

  #patchAudio(control, item) {
    const key = this.#rows.audioKey(item);
    if (control.dataset.controlKey === key) return;
    this.#audioLoader.release(control);
    control.innerHTML = this.#rows.audioMarkup(item);
    control.dataset.controlKey = key;
    this.#audioLoader.prepare(control);
  }

  #renderFull(job) {
    const items = this.#rows.models(job).map((item) => this.#rows.markup(item)).join("");
    this.#audioLoader.release(this.#root);
    this.#root.innerHTML = this.#detailMarkup(job, items);
    this.#root.dataset.jobId = job.id;
    this.#audioLoader.prepare(this.#root);
  }

  #replaceRow(itemId) {
    const row = $$(".audio-item", this.#root).find(
      (item) => item.dataset.itemId === String(itemId),
    );
    if (!row || !this.#job) return;
    const model = this.#rows.models(this.#job).find(
      (item) => String(item.id) === String(itemId),
    );
    if (!model) return;
    this.#audioLoader.release(row);
    row.outerHTML = this.#rows.markup(model);
    const next = $$(".audio-item", this.#root).find(
      (item) => item.dataset.itemId === String(itemId),
    );
    if (next) this.#audioLoader.prepare(next);
  }

  #detailMarkup(job, items) {
    const canDownload = (job.items || []).some((item) => item.audio_url)
      && !isActiveJob(job);
    return `
      <div class="detail-content">
        <header class="detail-header">
          <div class="detail-heading">
            <div class="detail-status-line">
              <span class="status ${escapeHtml(job.status)}" data-job-status>${escapeHtml(statusText(job.status))}</span>
              <span class="current-version">输出 <strong>GPT-SoVITS 原音</strong></span>
            </div>
            <h2 data-job-title title="${escapeHtml(job.name)}">${escapeHtml(job.name)}</h2>
          </div>
          <div class="detail-actions">
            <span class="accepted-summary" data-accepted-count>${job.accepted_items || 0}/${job.total_items} 已采用</span>
            <button class="secondary-button" type="button" data-detail-action="rename">重命名</button>
            ${job.can_export ? `<a class="download-button formal-export" data-formal-export href="${escapeHtml(job.export_url)}" download>正式导出</a>` : '<span class="export-hint" data-formal-export-hint>逐句采用后可正式导出</span>'}
            ${canDownload ? this.#downloadMarkup(job) : ""}
          </div>
        </header>
        ${this.#overviewMarkup(job)}
        ${this.#progressMarkup(job)}
        ${job.error ? `<pre class="detail-error" data-detail-error>${escapeHtml(job.error)}</pre>` : ""}
        <div class="script-heading"><span>序号</span><span>台本 / 发音 / 候选</span><span>试听与操作</span></div>
        <div class="audio-items">${items}</div>
      </div>
    `;
  }

  #overviewMarkup(job) {
    const ownerLabel = this.#state.isAdmin ? "提交用户" : "任务编号";
    const ownerValue = this.#state.isAdmin ? job.client_id : job.id;
    return `
      <div class="job-overview">
        <div class="overview-cell"><span>声音库</span><strong data-overview-value="voice">${escapeHtml(job.voice_name)}</strong></div>
        <div class="overview-cell"><span>克隆模型</span><strong data-overview-value="model">${escapeHtml(this.#modelLabel(job.model_id))}</strong></div>
        <div class="overview-cell"><span>输出方式</span><strong>原音直出</strong></div>
        <div class="overview-cell"><span>参考语气</span><strong>${escapeHtml(this.#emotionLabel(job.reference_emotion))}</strong></div>
        <div class="overview-cell"><span>${ownerLabel}</span><code data-overview-value="owner">${escapeHtml(ownerValue)}</code></div>
      </div>
    `;
  }

  #progressMarkup(job) {
    return `
      <div class="detail-progress">
        <div class="progress-track"><span class="progress-value" data-job-progress style="width:${safeProgress(job)}%"></span></div>
        <div class="progress-copy"><span data-progress-count>${job.completed_items}/${job.total_items} 段</span><span data-progress-hint>${escapeHtml(jobHint(job))}</span></div>
      </div>
    `;
  }

  #updateStatus(element, status) {
    const className = normalizedStatus(status);
    element.className = `status${className ? ` ${className}` : ""}`;
    element.textContent = statusText(status);
  }

  #updateDownload(job) {
    const actions = $(".detail-actions", this.#root);
    const allowed = (job.items || []).some((item) => item.audio_url) && !isActiveJob(job);
    let link = $("[data-download-all]", actions);
    if (!allowed) {
      link?.remove();
      return;
    }
    if (!link) {
      link = document.createElement("a");
      link.className = "download-button";
      link.dataset.downloadAll = "";
      link.download = "";
      link.textContent = "全部下载";
      actions.append(link);
    }
    link.href = job.download_url;
  }

  #updateFormalExport(job) {
    const actions = $(".detail-actions", this.#root);
    const link = $("[data-formal-export]", actions);
    const hint = $("[data-formal-export-hint]", actions);
    if (job.can_export) {
      if (link) link.href = job.export_url;
      else if (hint) {
        hint.outerHTML = `<a class="download-button formal-export" data-formal-export href="${escapeHtml(job.export_url)}" download>正式导出</a>`;
      }
    } else {
      link?.remove();
      if (!hint) actions.insertAdjacentHTML("beforeend", '<span class="export-hint" data-formal-export-hint>逐句采用后可正式导出</span>');
    }
  }

  #downloadMarkup(job) {
    return `<a class="download-button" data-download-all href="${escapeHtml(job.download_url)}" download>全部下载</a>`;
  }

  #modelLabel(modelId) {
    return this.#state.model(modelId)?.label || modelId;
  }

  #emotionLabel(emotion) {
    if (!emotion || emotion === "all") return "自动组合";
    return this.#state.config.reference_emotions?.find((item) => item.id === emotion)?.label || emotion;
  }
}
