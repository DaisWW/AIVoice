import { $, $$, escapeHtml, renderSelect, setButtonBusy } from "../core/dom.js";
import { GenerationSettingsControls } from "./generation-settings-controls.js";

const GENERATION_INPUTS = Object.freeze({
  temperature: "createTemperature",
  speed_factor: "createSpeed",
  top_k: "createTopK",
  top_p: "createTopP",
  repetition_penalty: "createPenalty",
});

export class GenerationController {
  #state;
  #api;
  #shell;
  #jobs;
  #onManageVoices;
  #controls;

  constructor({ state, api, shell, jobs, onManageVoices }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#jobs = jobs;
    this.#onManageVoices = onManageVoices;
    this.#controls = new GenerationSettingsControls(state, GENERATION_INPUTS);
  }

  bind() {
    $("#openCreate").addEventListener("click", () => this.open());
    $("#closeCreate").addEventListener("click", () => this.close());
    $("#drawerBackdrop").addEventListener("click", () => this.close());
    $("#drawerLibraryLink").addEventListener("click", () => this.#manageVoices());
    $("#jobForm").addEventListener("submit", (event) => this.#submit(event));
    $("#scriptSelect").addEventListener("change", () => this.applyScriptDefaults());
    $("#modelSelect").addEventListener("change", () => this.#applyModelDefaults());
    $("#modelCompareList").addEventListener("change", (event) => {
      if (event.target.matches('input[type="checkbox"]')) this.#limitCompareModels(event.target);
    });
    this.#controls.bind();
    $("#scriptFile").addEventListener("change", (event) => {
      $("#scriptFileName").textContent = event.target.files[0]?.name || "选择台本文件";
    });
    $$('[data-script-mode]').forEach((button) => {
      button.addEventListener("click", () => this.setScriptMode(button.dataset.scriptMode));
    });
    $$('[data-generation-mode]').forEach((button) => {
      button.addEventListener("click", () => this.#setGenerationMode(button.dataset.generationMode));
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
    renderSelect(
      $("#modelSelect"),
      this.#state.config.models,
      "",
      (item) => item.available === false ? `${item.label} · 待安装` : item.label,
    );
    renderSelect(
      $("#referenceEmotionSelect"),
      [{ id: "all", label: "自动组合" }, ...(this.#state.config.reference_emotions || [])],
      "",
      (item) => item.label,
    );
    this.#selectFirstOption($("#modelSelect"), this.#state.config.models);
    if (!$("#referenceEmotionSelect").value) $("#referenceEmotionSelect").value = "all";
    this.#controls.configure();
    this.#applyModelDefaults();
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
    drawer.hidden = false;
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
      if (!drawer.classList.contains("open")) {
        drawer.hidden = true;
        backdrop.hidden = true;
      }
    }, 220);
  }

  async reloadScripts() {
    const { scripts } = await this.#api.get(
      `/api/scripts?project_id=${encodeURIComponent(this.#state.projectId || "")}`,
    );
    this.#state.scripts = scripts;
    this.renderOptions();
  }

  #selectFirstOption(select, options) {
    const first = options.find((item) => item.available !== false);
    if (!select.value && first) select.value = first.id;
  }

  #updateDescription() {
    const model = this.#state.model($("#modelSelect").value);
    $("#modelDescription").textContent = model?.available === false
      ? model.availability_reason || "当前服务器尚未安装该模型"
      : model?.description || "";
    $("#modelOutputNote").textContent = `直接输出 ${model?.label || "模型"} 原音，不进行降噪、变调、EQ、压缩、混响或响度处理。`;
  }

  #applyModelDefaults() {
    this.#updateDescription();
    const model = this.#state.model($("#modelSelect").value);
    this.#renderCompareModels();
    this.#controls.setSupported(model?.generation_parameters);
    this.#controls.set({
      ...(this.#state.config.generation_defaults || {}),
      ...(model?.generation_defaults || {}),
    });
  }

  #renderCompareModels() {
    const primaryId = $("#modelSelect").value;
    const selected = new Set(this.#compareModelIds());
    selected.delete(primaryId);
    $("#modelCompareList").innerHTML = this.#state.config.models
      .filter((model) => model.id !== primaryId)
      .map((model) => this.#compareOption(model, selected.has(model.id)))
      .join("");
    this.#updateCompareHint();
  }

  #compareOption(model, checked) {
    const available = model.available !== false;
    const detail = available
      ? [model.stage, model.description].filter(Boolean).join(" · ")
      : model.availability_reason || "当前服务器尚未安装该模型";
    return `
      <label class="model-compare-option${available ? "" : " unavailable"}" title="${escapeHtml(detail)}">
        <input type="checkbox" value="${escapeHtml(model.id)}"${checked && available ? " checked" : ""}${available ? "" : " disabled"}>
        <span><strong>${escapeHtml(model.label)}</strong><small>${escapeHtml(detail)}</small></span>
      </label>`;
  }

  #limitCompareModels(input) {
    if (this.#compareModelIds().length > 3) {
      input.checked = false;
      this.#shell.toast("同时最多对比 4 个模型（含主模型）", true);
    }
    this.#updateCompareHint();
  }

  #compareModelIds() {
    return $$(`#modelCompareList input[type="checkbox"]:checked`).map((input) => input.value);
  }

  #updateCompareHint() {
    const count = this.#compareModelIds().length;
    $("#modelCompareHint").textContent = count
      ? `本次将创建 ${count + 1} 个任务；其他模型采用各自预设，并共享候选种子。`
      : "其他模型采用各自预设，并与主模型共享候选种子。";
  }

  #setGenerationMode(mode) {
    const advanced = mode === "advanced";
    $$('[data-generation-mode]').forEach((button) => {
      const active = button.dataset.generationMode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("#createAdvancedSettings").hidden = !advanced;
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
      const { job, jobs = [job] } = await this.#api.postForm("/api/jobs", this.#formData());
      this.#state.selectJob(job.id);
      this.#resetForm();
      this.close();
      this.#shell.showView("workspace");
      await Promise.all([this.#jobs.refresh(true), this.reloadScripts()]);
      this.#jobs.schedule();
      this.#shell.toast(jobs.length > 1 ? `${jobs.length} 个对比任务已加入队列` : "任务已加入生成队列");
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
    data.set("model_ids", JSON.stringify(this.#compareModelIds()));
    data.set("reference_emotion", $("#referenceEmotionSelect").value || "all");
    data.set("candidate_count", $("#candidateCount").value || "2");
    data.set("project_id", this.#state.projectId || "");
    data.set("generation_settings", JSON.stringify(this.#controls.values()));
    const baseSeed = $("#createBaseSeed").value.trim();
    if (baseSeed) data.set("base_seed", baseSeed);
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
    $("#createBaseSeed").value = "";
    $$(`#modelCompareList input[type="checkbox"]`).forEach((input) => {
      input.checked = false;
    });
    this.#setGenerationMode("simple");
    this.#applyModelDefaults();
  }
}
