import { $, $$ } from "../core/dom.js";
import {
  formatDate,
  isActiveJob,
  jobHint,
  normalizedStatus,
  safeProgress,
  statusText,
} from "../core/formatters.js";

export class JobListView {
  #state;
  #list;

  constructor(state) {
    this.#state = state;
    this.#list = $("#jobList");
  }

  render() {
    const jobs = this.#filteredJobs();
    $("#jobCount").textContent = this.#state.jobs.length;
    if (!jobs.length) {
      this.#renderEmpty();
      return;
    }
    $(".list-empty", this.#list)?.remove();
    const existing = new Map(
      $$(".task-entry", this.#list).map((entry) => [entry.dataset.jobId, entry]),
    );
    jobs.forEach((job, index) => this.#placeJob(job, index, existing));
    existing.forEach((entry) => entry.remove());
  }

  #filteredJobs() {
    if (this.#state.filter === "active") return this.#state.jobs.filter(isActiveJob);
    if (this.#state.filter === "completed") {
      return this.#state.jobs.filter((job) => job.status === "completed");
    }
    return this.#state.jobs;
  }

  #renderEmpty() {
    if (!$(".list-empty", this.#list) || $(".task-entry", this.#list)) {
      this.#list.innerHTML = '<div class="list-empty">当前没有生成记录</div>';
    }
  }

  #placeJob(job, index, existing) {
    const entry = existing.get(job.id) || this.#createEntry();
    this.#updateEntry(entry, job);
    const current = this.#list.children[index];
    if (current !== entry) this.#list.insertBefore(entry, current || null);
    existing.delete(job.id);
  }

  #createEntry() {
    const entry = document.createElement("article");
    entry.className = "task-entry";
    entry.innerHTML = `
      <button class="task-select" type="button" data-action="select-job">
        <span class="task-top">
          <strong class="task-title"></strong>
          <span class="status"></span>
        </span>
        <span class="task-meta"></span>
        <span class="progress-track"><span class="progress-value"></span></span>
        <span class="task-bottom"><span></span><span></span></span>
      </button>
      <button class="rename-task" type="button" data-action="rename-job" title="重命名任务">重命名</button>
    `;
    return entry;
  }

  #updateEntry(entry, job) {
    const renderKey = this.#renderKey(job);
    if (entry.dataset.renderKey === renderKey) return;
    const active = job.id === this.#state.selectedJobId;
    const selectButton = $(".task-select", entry);
    entry.dataset.jobId = job.id;
    entry.classList.toggle("active", active);
    selectButton.dataset.jobId = job.id;
    selectButton.setAttribute("aria-current", String(active));
    $(".rename-task", entry).dataset.jobId = job.id;
    $(".task-title", entry).textContent = job.name;
    this.#updateStatus($(".status", entry), job.status);
    $(".task-meta", entry).textContent = this.#meta(job);
    $(".progress-value", entry).style.width = `${safeProgress(job)}%`;
    const bottom = $$(".task-bottom > span", entry);
    bottom[0].textContent = `${job.completed_items}/${job.total_items}`;
    bottom[1].textContent = jobHint(job);
    entry.dataset.renderKey = renderKey;
  }

  #updateStatus(element, status) {
    const className = normalizedStatus(status);
    element.className = `status${className ? ` ${className}` : ""}`;
    element.textContent = statusText(status);
  }

  #meta(job) {
    const owner = this.#state.isAdmin ? `${job.client_id} · ` : "";
    return `${owner + job.voice_name} · ${formatDate(job.submitted_at)}`;
  }

  #renderKey(job) {
    return JSON.stringify([
      job.name,
      job.status,
      this.#state.isAdmin ? job.client_id : "",
      job.voice_name,
      job.submitted_at,
      job.completed_items,
      job.total_items,
      safeProgress(job),
      jobHint(job),
      job.id === this.#state.selectedJobId,
    ]);
  }
}
