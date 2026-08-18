import { $, $$, setButtonBusy } from "../core/dom.js";
import { isActiveJob } from "../core/formatters.js";

export class JobController {
  #state;
  #api;
  #shell;
  #listView;
  #detailView;
  #candidates = null;
  #selectedJob = null;
  #request = null;
  #refreshTimer = null;
  #refreshing = false;
  #renameJobId = null;

  constructor({ state, api, shell, listView, detailView }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#listView = listView;
    this.#detailView = detailView;
  }

  setCandidateController(controller) {
    this.#candidates = controller;
  }

  bind() {
    $("#jobList").addEventListener("click", (event) => this.#handleListClick(event));
    $("#jobDetail").addEventListener("click", (event) => this.#handleDetailClick(event));
    $("#renameForm").addEventListener("submit", (event) => this.#rename(event));
    $$(".filter-tab").forEach((button) => this.#bindFilter(button));
  }

  render() {
    this.#listView.render();
  }

  async selectInitial() {
    if (!this.#state.jobs.length) return;
    const selected = this.#state.jobs.find((job) => job.id === this.#state.selectedJobId);
    const fallback = this.#state.jobs.find(isActiveJob) || this.#state.jobs[0];
    await this.select((selected || fallback).id, false, false);
  }

  async select(jobId, focusDetail = false, switchView = true) {
    const changed = this.#state.selectedJobId !== jobId;
    this.#state.selectJob(jobId);
    if (changed) {
      this.#selectedJob = null;
    }
    if (switchView) this.#shell.showView("workspace");
    this.#scrollToDetail(focusDetail);
    this.#listView.render();
    if (this.#detailView.hasJob(jobId)) {
      this.#cancelSelection();
      this.#detailView.setLoading(false);
      return;
    }
    await this.#loadSelection(jobId);
  }

  async refresh(forceDetail = false) {
    const projectId = this.#state.projectId;
    if (!projectId || this.#refreshing || (document.hidden && !forceDetail)) return;
    this.#refreshing = true;
    try {
      const { jobs } = await this.#api.get(
        `${this.#state.jobApiBase}?limit=300&project_id=${encodeURIComponent(projectId)}`,
      );
      if (this.#state.projectId !== projectId) return;
      this.#state.jobs = jobs;
      this.#listView.render();
      await this.#syncSelection(forceDetail);
    } catch (_) {
      this.#shell.renderOffline();
    } finally {
      this.#refreshing = false;
    }
  }

  schedule(delay) {
    clearTimeout(this.#refreshTimer);
    if (!this.#state.projectId) return;
    const idleDelay = this.#state.isAdmin ? 4000 : 6000;
    const nextDelay = delay ?? (this.#state.jobs.some(isActiveJob) ? 1200 : idleDelay);
    this.#refreshTimer = setTimeout(async () => {
      await this.refresh(false);
      this.schedule();
    }, nextDelay);
  }

  async onVisible() {
    await this.refresh(false);
    this.schedule();
  }

  openRename(jobId) {
    const job = this.#state.jobs.find((item) => item.id === jobId);
    if (!job) return;
    this.#renameJobId = jobId;
    $("#renameInput").value = job.name;
    $("#renameDialog").showModal();
    $("#renameInput").select();
  }

  #handleListClick(event) {
    const control = event.target.closest("[data-action]");
    if (!control) return;
    if (control.dataset.action === "select-job") this.select(control.dataset.jobId, true);
    if (control.dataset.action === "rename-job") this.openRename(control.dataset.jobId);
    if (control.dataset.action === "delete-job") this.#delete(control.dataset.jobId);
  }

  #handleDetailClick(event) {
    const candidateControl = event.target.closest("[data-candidate-action]");
    if (candidateControl) {
      this.#handleCandidateAction(candidateControl.dataset.candidateAction, candidateControl);
      return;
    }
    const control = event.target.closest("[data-detail-action]");
    if (!control) return;
    if (control.dataset.detailAction === "rename" && this.#state.selectedJobId) {
      this.openRename(this.#state.selectedJobId);
    }
    if (control.dataset.detailAction === "delete" && this.#state.selectedJobId) {
      this.#delete(this.#state.selectedJobId);
    }
  }

  #handleCandidateAction(action, control) {
    const job = this.#selectedJob;
    const item = job?.items?.find((row) => String(row.id) === String(control.dataset.itemId));
    const candidate = item?.candidates?.find(
      (row) => row.id === control.dataset.candidateId,
    ) || item?.candidates?.find((row) => row.accepted) || item?.candidates?.[0];
    if (!job || !item) return;
    if (action === "select") {
      this.#detailView.selectCandidate(item.id, control.dataset.candidateId);
      return;
    }
    if (action === "regenerate") {
      this.#candidates?.openRegenerate(job, item, candidate);
      return;
    }
    if (action === "accept" && candidate) this.#acceptCandidate(item, candidate);
  }

  async #acceptCandidate(item, candidate) {
    if (!this.#selectedJob) return;
    try {
      await this.#api.post(
        `${this.#state.jobApiBase}/${encodeURIComponent(this.#selectedJob.id)}/items/${encodeURIComponent(item.id)}/accept`,
        { candidate_id: candidate.id },
      );
      this.#shell.toast(`已采用第 ${item.sequence} 段的 ${candidate.name}`);
      await this.refresh(true);
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
  }

  #bindFilter(button) {
    button.setAttribute("aria-selected", String(button.classList.contains("active")));
    button.addEventListener("click", () => {
      this.#state.filter = button.dataset.filter;
      $$(".filter-tab").forEach((item) => {
        const active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", String(active));
      });
      this.#listView.render();
    });
  }

  #scrollToDetail(enabled) {
    if (enabled && window.matchMedia("(max-width: 760px)").matches) {
      $(".main-stage").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async #loadSelection(jobId) {
    this.#cancelSelection();
    const controller = new AbortController();
    this.#request = controller;
    this.#detailView.setLoading(true);
    this.#detailView.showInitialLoading();
    try {
      const { job } = await this.#api.get(
        `${this.#state.jobApiBase}/${encodeURIComponent(jobId)}`,
        { signal: controller.signal },
      );
      if (this.#state.selectedJobId === jobId) this.#renderSelection(job);
    } catch (error) {
      if (error.name !== "AbortError") this.#shell.toast(error.message, true);
    } finally {
      this.#finishSelection(controller, jobId);
    }
  }

  #finishSelection(controller, jobId) {
    if (this.#request !== controller) return;
    this.#request = null;
    if (this.#state.selectedJobId === jobId) this.#detailView.setLoading(false);
  }

  #cancelSelection() {
    this.#request?.abort();
  }

  async #syncSelection(forceDetail) {
    const selected = this.#state.jobs.find((job) => job.id === this.#state.selectedJobId);
    if (!selected) {
      await this.#selectFallback();
      return;
    }
    if ((forceDetail || isActiveJob(selected)) && !this.#request) {
      await this.#refreshDetail(selected.id);
    }
  }

  async #selectFallback() {
    if (this.#state.selectedJobId) {
      this.#state.selectJob(null);
      this.#selectedJob = null;
      this.#detailView.clear();
    }
    if (!this.#state.jobs.length) return;
    const fallback = this.#state.jobs.find(isActiveJob) || this.#state.jobs[0];
    await this.select(fallback.id, false, this.#state.view === "workspace");
  }

  async #refreshDetail(jobId) {
    const { job } = await this.#api.get(
      `${this.#state.jobApiBase}/${encodeURIComponent(jobId)}`,
    );
    if (this.#state.selectedJobId !== job.id) return;
    this.#renderSelection(job);
  }

  #renderSelection(job) {
    this.#selectedJob = job;
    this.#detailView.render(job);
  }

  async #rename(event) {
    event.preventDefault();
    const name = $("#renameInput").value.trim();
    if (!name || !this.#renameJobId) return;
    const button = $("button[type='submit']", event.currentTarget);
    setButtonBusy(button, true);
    try {
      const { job } = await this.#api.patch(
        `${this.#state.jobApiBase}/${encodeURIComponent(this.#renameJobId)}`,
        { name },
      );
      await this.#applyRename(job);
      $("#renameDialog").close();
      this.#shell.toast("任务已重命名");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #applyRename(job) {
    const index = this.#state.jobs.findIndex((item) => item.id === job.id);
    if (index >= 0) this.#state.jobs[index] = { ...this.#state.jobs[index], ...job };
    this.#listView.render();
    if (this.#state.selectedJobId === job.id) await this.#refreshDetail(job.id);
  }

  async #delete(jobId) {
    const job = this.#state.jobs.find((item) => item.id === jobId);
    if (!job || isActiveJob(job) || !window.confirm(`确定删除生成记录“${job.name}”吗？`)) return;
    try {
      await this.#api.delete(`${this.#state.jobApiBase}/${encodeURIComponent(jobId)}`);
      this.#state.jobs = this.#state.jobs.filter((item) => item.id !== jobId);
      if (this.#state.selectedJobId === jobId) {
        this.#cancelSelection();
        this.#state.selectJob(null);
        this.#selectedJob = null;
        this.#detailView.clear();
      }
      this.#listView.render();
      await this.#selectFallback();
      this.#shell.toast("生成记录已删除");
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
  }
}
