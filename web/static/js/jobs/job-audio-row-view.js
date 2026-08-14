import { escapeHtml, multiline } from "../core/dom.js";

const CANDIDATE_STATUS = {
  completed: "完成",
  failed: "失败",
  running: "生成中",
  queued: "排队",
};

export class JobAudioRowView {
  #candidateSelections = new Map();

  clear() {
    this.#candidateSelections.clear();
  }

  selectCandidate(itemId, candidateId) {
    this.#candidateSelections.set(itemId, candidateId);
  }

  models(job) {
    return (job.items || []).map((item) => this.#model(item));
  }

  markup(item) {
    return `
      <section class="audio-item" data-item-id="${escapeHtml(item.id)}"
        data-item-sequence="${escapeHtml(item.sequence)}" data-row-key="${escapeHtml(this.rowKey(item))}"
        data-text-key="${escapeHtml(JSON.stringify([item.displayText, item.displayPronunciation]))}">
        <span class="item-number">${String(item.sequence).padStart(2, "0")}</span>
        <div class="transcript">
          <p class="item-text">${multiline(item.displayText)}</p>
          <code class="pronunciation">${multiline(item.displayPronunciation)}</code>
          ${this.#candidateMarkup(item)}
        </div>
        <div class="audio-control" data-control-key="${escapeHtml(this.audioKey(item))}">
          ${this.audioMarkup(item)}
        </div>
      </section>
    `;
  }

  audioKey(item) {
    return JSON.stringify([
      item.displayAudioUrl,
      item.displayDownloadUrl,
      item.displayDuration,
      item.activeCandidate?.status,
      item.activeCandidate?.error,
      item.status,
      item.error,
    ]);
  }

  rowKey(item) {
    return JSON.stringify([
      item.activeCandidate?.id || "",
      item.activeCandidate?.status || item.status,
      item.activeCandidate?.audio_url || item.audio_url || "",
      item.activeCandidate?.accepted,
      item.candidates?.map((candidate) => [
        candidate.id,
        candidate.status,
        candidate.accepted,
        candidate.audio_url,
      ]) || [],
    ]);
  }

  audioMarkup(item) {
    if (!item.displayAudioUrl) return this.#emptyAudioMarkup(item);
    const duration = item.displayDuration
      ? `${Number(item.displayDuration).toFixed(1)} 秒`
      : "生成音频";
    const download = item.displayDownloadUrl
      ? `<a href="${escapeHtml(item.displayDownloadUrl)}" download>下载</a>`
      : "";
    return `
      <div class="audio-head"><span>${duration} · GPT-SoVITS 原音</span>${download}</div>
      <audio controls preload="none" data-lazy-audio src="${escapeHtml(item.displayAudioUrl)}"></audio>
    `;
  }

  #model(item) {
    const candidates = item.candidates || [];
    const activeCandidate = this.#activeCandidate(item, candidates);
    return {
      ...item,
      activeCandidate,
      candidates,
      displayText: activeCandidate?.text || item.text,
      displayPronunciation: activeCandidate?.pronunciation || item.pronunciation,
      displayAudioUrl: activeCandidate?.audio_url || item.audio_url,
      displayDownloadUrl: activeCandidate?.download_url || item.download_url,
      displayDuration: activeCandidate?.duration_seconds || item.duration_seconds,
    };
  }

  #activeCandidate(item, candidates) {
    const selectedId = this.#candidateSelections.get(item.id);
    const selected = candidates.find((candidate) => candidate.id === selectedId);
    const candidate = selected
      || candidates.find((row) => row.accepted)
      || candidates.find((row) => row.status === "completed")
      || candidates[0];
    if (candidate) this.#candidateSelections.set(item.id, candidate.id);
    return candidate;
  }

  #candidateMarkup(item) {
    const candidates = item.candidates || [];
    const chips = candidates.map((candidate) => this.#candidateChip(item, candidate)).join("");
    const empty = '<span class="candidate-empty">候选将在生成后出现</span>';
    return `
      <div class="candidate-panel">
        <div class="candidate-chips">${chips || empty}</div>
        ${this.#candidateActions(item)}
      </div>
    `;
  }

  #candidateChip(item, candidate) {
    const active = candidate.id === item.activeCandidate?.id;
    const accepted = candidate.accepted ? " accepted" : "";
    const check = candidate.accepted ? '<b title="正式采用">✓</b>' : "";
    return `
      <button class="candidate-chip${active ? " active" : ""}${accepted}" type="button"
        data-candidate-action="select" data-item-id="${escapeHtml(item.id)}"
        data-candidate-id="${escapeHtml(candidate.id)}" aria-pressed="${active}">
        <span>${escapeHtml(candidate.name)}</span>
        <small>${CANDIDATE_STATUS[candidate.status] || candidate.status}</small>${check}
      </button>
    `;
  }

  #candidateActions(item) {
    const candidate = item.activeCandidate;
    if (!item.candidates.length) return "";
    const itemId = escapeHtml(item.id);
    const candidateId = escapeHtml(candidate?.id || "");
    const accept = candidate?.status === "completed" && !candidate.accepted
      ? `<button type="button" class="row-action adopt" data-candidate-action="accept" data-item-id="${itemId}" data-candidate-id="${candidateId}">采用</button>`
      : "";
    return `
      <div class="candidate-actions">
        <button type="button" class="row-action" data-candidate-action="regenerate"
          data-item-id="${itemId}" data-candidate-id="${candidateId}">重做</button>
        ${accept}
      </div>
    `;
  }

  #emptyAudioMarkup(item) {
    if (item.activeCandidate?.status === "failed" || item.status === "failed") {
      return `<div class="item-failed">${escapeHtml(item.activeCandidate?.error || item.error || "生成失败")}</div>`;
    }
    const running = item.activeCandidate?.status === "running" || item.status === "running";
    return `<div class="item-pending">${running ? "正在生成" : "等待生成"}</div>`;
  }
}
