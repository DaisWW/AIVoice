import { $, $$, renderSelect, setButtonBusy } from "../core/dom.js";

export class GenerationController {
  #state;
  #api;
  #shell;
  #jobs;
  #onManageVoices;

  constructor({ state, api, shell, jobs, onManageVoices }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#jobs = jobs;
    this.#onManageVoices = onManageVoices;
  }

  bind() {
    $("#openCreate").addEventListener("click", () => this.open());
    $("#closeCreate").addEventListener("click", () => this.close());
    $("#drawerBackdrop").addEventListener("click", () => this.close());
    $("#drawerLibraryLink").addEventListener("click", () => this.#manageVoices());
    $("#jobForm").addEventListener("submit", (event) => this.#submit(event));
    $("#scriptSelect").addEventListener("change", () => this.applyScriptDefaults());
    $("#modelSelect").addEventListener("change", () => this.#updateDescription());
    $("#scriptFile").addEventListener("change", (event) => {
      $("#scriptFileName").textContent = event.target.files[0]?.name || "选择台本文件";
    });
    $$('[data-script-mode]').forEach((button) => {
      button.addEventListener("click", () => this.setScriptMode(button.dataset.scriptMode));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && $("#createDrawer").classList.contains("open")) {
        this.close();
      }
    });
  }

  renderOptions() {
    renderSelect(
      $("#scriptSelect"),
      this.#state.scripts,
      "选择台本",
      (item) => `${item.name} · ${item.item_count} 段`,
    );
    renderSelect(
      $("#voiceSelect"),
      this.#state.voices,
      "选择声音库",
      (item) => `${item.name} · ${item.enabled_file_count || 0} 条启用`,
    );
    renderSelect($("#modelSelect"), this.#state.config.models, "", (item) => item.label);
    renderSelect(
      $("#referenceEmotionSelect"),
      [{ id: "all", label: "自动组合" }, ...(this.#state.config.reference_emotions || [])],
      "",
      (item) => item.label,
    );
    this.#selectFirstOption($("#modelSelect"), this.#state.config.models);
    if (!$("#referenceEmotionSelect").value) $("#referenceEmotionSelect").value = "all";
    this.#updateDescription();
  }

  setScriptMode(mode) {
    this.#state.scriptMode = mode;
    $$('[data-script-mode]').forEach((button) => {
      const active = button.dataset.scriptMode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const existing = mode === "existing";
    $("#existingScriptField").classList.toggle("active", existing);
    $("#uploadScriptField").classList.toggle("active", !existing);
    $("#scriptSelect").disabled = !existing;
    $("#scriptSelect").required = existing;
    $("#scriptFile").disabled = existing;
    $("#scriptFile").required = !existing;
  }

  applyScriptDefaults() {
    const script = this.#state.scripts.find(
      (item) => item.id === $("#scriptSelect").value,
    );
    if (script && this.#state.voices.some((item) => item.id === script.default_voice_id)) {
      $("#voiceSelect").value = script.default_voice_id;
    }
  }

  open() {
    const drawer = $("#createDrawer");
    const backdrop = $("#drawerBackdrop");
    backdrop.hidden = false;
    requestAnimationFrame(() => backdrop.classList.add("visible"));
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    setTimeout(() => $("#taskName").focus(), 220);
  }

  close() {
    const drawer = $("#createDrawer");
    const backdrop = $("#drawerBackdrop");
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    backdrop.classList.remove("visible");
    setTimeout(() => {
      if (!drawer.classList.contains("open")) backdrop.hidden = true;
    }, 220);
  }

  async reloadScripts() {
    const { scripts } = await this.#api.get("/api/scripts");
    this.#state.scripts = scripts;
    this.renderOptions();
  }

  #selectFirstOption(select, options) {
    if (!select.value && options.length) select.value = options[0].id;
  }

  #updateDescription() {
    $("#modelDescription").textContent =
      this.#state.model($("#modelSelect").value)?.description || "";
  }

  #manageVoices() {
    this.close();
    this.#onManageVoices();
  }

  async #submit(event) {
    event.preventDefault();
    if (!this.#validate()) return;
    const button = $("#submitJob");
    setButtonBusy(button, true);
    button.firstElementChild.textContent = "正在提交";
    try {
      const { job } = await this.#api.postForm("/api/jobs", this.#formData());
      this.#state.selectJob(job.id);
      this.#resetForm();
      this.close();
      this.#shell.showView("workspace");
      await Promise.all([this.#jobs.refresh(true), this.reloadScripts()]);
      this.#jobs.schedule();
      this.#shell.toast("任务已加入生成队列");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
      button.firstElementChild.textContent = "加入生成队列";
    }
  }

  #validate() {
    const scriptFile = $("#scriptFile").files[0];
    if (this.#state.scriptMode === "existing" && !$("#scriptSelect").value) {
      this.#shell.toast("请选择台本", true);
      return false;
    }
    if (this.#state.scriptMode === "upload" && !scriptFile) {
      this.#shell.toast("请选择台本文件", true);
      return false;
    }
    if (!$("#voiceSelect").value) {
      this.#shell.toast("请选择声音库", true);
      return false;
    }
    return true;
  }

  #formData() {
    const data = new FormData();
    data.set("name", $("#taskName").value);
    data.set("voice_id", $("#voiceSelect").value);
    data.set("model_id", $("#modelSelect").value);
    data.set("reference_emotion", $("#referenceEmotionSelect").value || "all");
    data.set("candidate_count", $("#candidateCount").value || "2");
    if (this.#state.scriptMode === "existing") {
      data.set("script_id", $("#scriptSelect").value);
    } else {
      data.set("script", $("#scriptFile").files[0]);
    }
    return data;
  }

  #resetForm() {
    $("#taskName").value = "";
    $("#scriptFile").value = "";
    $("#scriptFileName").textContent = "选择台本文件";
  }
}
