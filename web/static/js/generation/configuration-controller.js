import { escapeHtml } from "../core/dom.js";


const byId = (id) => document.getElementById(id);


export class GenerationConfigurationController {
  constructor(state, { storePosition, toast }) {
    this.state = state;
    this.storePosition = storePosition;
    this.toast = toast;
    this.sequence = 0;
  }

  restore(configurations, voices, models) {
    const voiceIds = new Set(voices.map((voice) => voice.id));
    const modelIds = new Set(models.map((model) => model.id));
    const combinations = new Set();
    this.state.generationConfigurations = Array.isArray(configurations)
      ? configurations.flatMap((configuration) => {
        if (!configuration || !voiceIds.has(configuration.voiceId) || !modelIds.has(configuration.modelId)) return [];
        const combination = `${configuration.voiceId}\u0000${configuration.modelId}`;
        if (combinations.has(combination) || combinations.size >= 4) return [];
        combinations.add(combination);
        return [{
          id: String(++this.sequence),
          voiceId: configuration.voiceId,
          modelId: configuration.modelId,
          candidateCount: Math.min(4, Math.max(1, Number(configuration.candidateCount) || 1)),
        }];
      })
      : [];
  }

  handleDocumentClick(event) {
    if (!event.target.closest(".generation-dropdown")) this.closeDropdowns();
  }

  handleAction(action, button) {
    if (action === "generate-all") this.open(null);
    else if (action === "generate-line") this.open(Number(button.dataset.lineNumber));
    else if (action === "add-generation-configuration") this.add();
    else if (action === "remove-generation-configuration") this.remove(button.dataset.configurationId);
    else if (action === "toggle-generation-dropdown") this.toggleDropdown(button);
    else if (action === "select-generation-dropdown-option") this.selectDropdownOption(button);
    else return false;
    return true;
  }

  handleInput(input) {
    if (input.dataset.action === "generation-dropdown-search") this.filterDropdown(input);
  }

  handleKeydown(event) {
    if (event.key !== "Escape") return false;
    const dropdown = event.target.closest?.(".generation-dropdown");
    if (!dropdown || dropdown.querySelector(".generation-dropdown-panel")?.hidden) return false;
    event.preventDefault();
    event.stopPropagation();
    const trigger = dropdown.querySelector(".generation-dropdown-trigger");
    this.closeDropdowns();
    trigger?.focus();
    return true;
  }

  open(lineNumber = null) {
    const script = this.state.scripts.find((item) => item.id === this.state.selectedScriptId);
    const voices = this.availableVoices();
    const models = this.availableModels();
    if (!script || !voices.length || !models.length) {
      this.toast("请先添加可用声音并配置可用模型", true);
      return;
    }
    const voiceIds = new Set(voices.map((voice) => voice.id));
    const modelIds = new Set(models.map((model) => model.id));
    this.state.generationRequest = { lineNumber };
    this.state.generationConfigurations = this.state.generationConfigurations.filter(
      (configuration) => voiceIds.has(configuration.voiceId) && modelIds.has(configuration.modelId),
    );
    if (!this.state.generationConfigurations.length) {
      this.state.generationConfigurations.push({
        id: String(++this.sequence),
        voiceId: voiceIds.has(this.state.selectedVoiceId) ? this.state.selectedVoiceId : voices[0].id,
        modelId: modelIds.has(this.state.selectedModelId) ? this.state.selectedModelId : models[0].id,
        candidateCount: 1,
      });
    }
    byId("generationOptionsTitle").textContent = lineNumber === null ? "全部生成" : `第 ${lineNumber} 行生成`;
    this.render();
    this.storePosition();
    byId("generationOptionsDialog").showModal();
  }

  submission() {
    const lineNumber = this.state.generationRequest?.lineNumber ?? null;
    const configurations = this.state.generationConfigurations.map((configuration) => ({ ...configuration }));
    if (!configurations.length || configurations.some((configuration) => !configuration.voiceId || !configuration.modelId)) {
      this.toast("请完善至少一条生成配置", true);
      return null;
    }
    return { lineNumber, configurations };
  }

  finish() {
    byId("generationOptionsDialog").close();
    this.state.generationRequest = null;
  }

  closeDropdowns(except = null) {
    document.querySelectorAll(".generation-dropdown").forEach((dropdown) => {
      if (dropdown === except) return;
      const panel = dropdown.querySelector(".generation-dropdown-panel");
      const trigger = dropdown.querySelector(".generation-dropdown-trigger");
      if (panel) panel.hidden = true;
      trigger?.setAttribute("aria-expanded", "false");
    });
  }

  availableVoices() {
    return this.state.voices.filter((voice) => voice.enabled_file_count);
  }

  availableModels() {
    return this.state.config.models.filter((model) => model.available === true);
  }

  voiceLabels(voices) {
    const nameCounts = voices.reduce(
      (counts, voice) => counts.set(voice.name, (counts.get(voice.name) || 0) + 1),
      new Map(),
    );
    return new Map(voices.map((voice) => [
      voice.id,
      nameCounts.get(voice.name) > 1 ? `${voice.name} · ${voice.id.slice(0, 6)}` : voice.name,
    ]));
  }

  dropdown(configuration, index, field, selectedValue, options, placeholder) {
    const selected = options.find((option) => String(option.value) === String(selectedValue));
    const name = `${field}-${configuration.id}`;
    return `<div class="generation-dropdown" data-generation-dropdown="${escapeHtml(name)}"><button class="generation-dropdown-trigger" type="button" data-action="toggle-generation-dropdown" aria-haspopup="listbox" aria-expanded="false" aria-controls="generation-dropdown-panel-${escapeHtml(name)}" aria-label="第 ${index + 1} 条配置的${escapeHtml(field)}"><span>${escapeHtml(selected?.label || placeholder)}</span><span class="generation-dropdown-chevron" aria-hidden="true">⌄</span></button><div id="generation-dropdown-panel-${escapeHtml(name)}" class="generation-dropdown-panel" hidden><input class="generation-dropdown-search" type="search" autocomplete="off" placeholder="搜索${escapeHtml(field)}" data-action="generation-dropdown-search" aria-label="搜索第 ${index + 1} 条配置的${escapeHtml(field)}"><div class="generation-dropdown-options" role="listbox" aria-label="第 ${index + 1} 条配置的${escapeHtml(field)}选项">${options.map((option) => `<button class="generation-dropdown-option${String(option.value) === String(selectedValue) ? " selected" : ""}" type="button" role="option" aria-selected="${String(option.value) === String(selectedValue)}" data-action="select-generation-dropdown-option" data-configuration-id="${escapeHtml(configuration.id)}" data-field="${escapeHtml(field)}" data-value="${escapeHtml(option.value)}" data-search-text="${escapeHtml(`${option.label} ${option.description || ""}`.toLocaleLowerCase())}"><strong>${escapeHtml(option.label)}</strong>${option.description ? `<small>${escapeHtml(option.description)}</small>` : ""}<span class="generation-dropdown-check" aria-hidden="true">✓</span></button>`).join("")}</div><p class="generation-dropdown-empty" hidden>没有匹配项</p></div></div>`;
  }

  toggleDropdown(trigger) {
    const dropdown = trigger.closest(".generation-dropdown");
    const panel = dropdown?.querySelector(".generation-dropdown-panel");
    if (!dropdown || !panel) return;
    const opening = panel.hidden;
    this.closeDropdowns(dropdown);
    panel.hidden = !opening;
    trigger.setAttribute("aria-expanded", String(opening));
    if (!opening) return;
    const search = panel.querySelector(".generation-dropdown-search");
    search.value = "";
    this.filterDropdown(search);
    this.positionDropdown(trigger, panel);
    search.focus();
  }

  positionDropdown(trigger, panel) {
    const rect = trigger.getBoundingClientRect();
    const margin = 10;
    const width = Math.min(Math.max(rect.width, 250), window.innerWidth - margin * 2);
    const left = Math.min(Math.max(rect.left, margin), window.innerWidth - width - margin);
    panel.style.width = `${width}px`;
    panel.style.left = `${left}px`;
    panel.style.right = "auto";
    panel.style.top = `${rect.bottom + 6}px`;
    panel.style.bottom = "auto";
    const panelHeight = panel.getBoundingClientRect().height;
    const spaceBelow = window.innerHeight - rect.bottom - margin;
    const spaceAbove = rect.top - margin;
    const openAbove = panelHeight > spaceBelow && spaceAbove > spaceBelow;
    const available = Math.max(100, (openAbove ? spaceAbove : spaceBelow) - 12);
    panel.querySelector(".generation-dropdown-options").style.maxHeight = `${Math.max(70, available - 55)}px`;
    if (openAbove) {
      panel.style.top = "auto";
      panel.style.bottom = `${window.innerHeight - rect.top + 6}px`;
    }
  }

  filterDropdown(input) {
    const panel = input.closest(".generation-dropdown-panel");
    if (!panel) return;
    const query = input.value.trim().toLocaleLowerCase();
    let visible = 0;
    panel.querySelectorAll(".generation-dropdown-option").forEach((option) => {
      option.hidden = Boolean(query) && !option.dataset.searchText.includes(query);
      if (!option.hidden) visible += 1;
    });
    panel.querySelector(".generation-dropdown-empty").hidden = visible > 0;
  }

  selectDropdownOption(option) {
    const configuration = this.state.generationConfigurations.find(
      (item) => item.id === option.dataset.configurationId,
    );
    if (!configuration) return;
    const nextVoiceId = option.dataset.field === "原声" ? option.dataset.value : configuration.voiceId;
    const nextModelId = option.dataset.field === "模型" ? option.dataset.value : configuration.modelId;
    const duplicate = this.state.generationConfigurations.some(
      (item) => item !== configuration && item.voiceId === nextVoiceId && item.modelId === nextModelId,
    );
    if (duplicate) {
      this.toast("该原声与模型组合已存在", true);
      return;
    }
    configuration.voiceId = nextVoiceId;
    configuration.modelId = nextModelId;
    if (option.dataset.field === "条数") {
      configuration.candidateCount = Math.min(4, Math.max(1, Number(option.dataset.value) || 1));
    }
    this.storePosition();
    this.render();
  }

  render(focusId = null) {
    const voices = this.availableVoices();
    const models = this.availableModels();
    const voiceLabels = this.voiceLabels(voices);
    const voiceOptions = voices.map((voice) => ({
      value: voice.id,
      label: voiceLabels.get(voice.id),
      description: `${voice.enabled_file_count} 条可用录音`,
    }));
    const modelOptions = models.map((model) => ({
      value: model.id,
      label: model.label || model.id,
      description: model.engine || model.id,
    }));
    const countOptions = [1, 2, 3, 4].map((count) => ({
      value: String(count),
      label: `${count} 条`,
      description: "每行候选",
    }));
    byId("generationConfigurationList").innerHTML = this.state.generationConfigurations.length
      ? this.state.generationConfigurations.map((configuration, index) => {
        const voice = voices.find((item) => item.id === configuration.voiceId);
        const model = models.find((item) => item.id === configuration.modelId);
        return `<div class="generation-configuration" data-generation-configuration="${escapeHtml(configuration.id)}"><span class="generation-configuration-index">${String(index + 1).padStart(2, "0")}</span><div class="generation-configuration-field"><span>原声</span>${this.dropdown(configuration, index, "原声", configuration.voiceId, voiceOptions, "选择原声")}<small>${voice ? `${voice.enabled_file_count} 条可用录音` : "请选择原声"}</small></div><div class="generation-configuration-field"><span>模型</span>${this.dropdown(configuration, index, "模型", configuration.modelId, modelOptions, "选择模型")}<small>${escapeHtml(model?.engine || model?.id || "请选择模型")}</small></div><div class="generation-configuration-field generation-count-field"><span>条数</span>${this.dropdown(configuration, index, "条数", String(configuration.candidateCount), countOptions, "选择条数")}<small>每行候选</small></div><button class="icon-button generation-configuration-remove" type="button" data-action="remove-generation-configuration" data-configuration-id="${escapeHtml(configuration.id)}" title="移除这条配置" aria-label="移除第 ${index + 1} 条生成配置">×</button></div>`;
      }).join("")
      : `<div class="generation-configuration-empty"><strong>还没有生成配置</strong><span>点击“＋ 添加生成”开始组装。</span></div>`;
    this.updateCombinationCount();
    if (focusId) {
      byId("generationConfigurationList")
        .querySelector(`[data-generation-configuration="${focusId}"] .generation-dropdown-trigger`)
        ?.focus();
    }
  }

  add() {
    const voices = this.availableVoices();
    const models = this.availableModels();
    if (!voices.length || !models.length) return;
    if (this.state.generationConfigurations.length >= 4) {
      this.toast("最多添加 4 条生成配置", true);
      return;
    }
    const used = new Set(this.state.generationConfigurations.map((item) => `${item.voiceId}\u0000${item.modelId}`));
    const previous = this.state.generationConfigurations.at(-1);
    const fallbackVoice = voices.some((voice) => voice.id === this.state.selectedVoiceId) ? this.state.selectedVoiceId : voices[0].id;
    const fallbackModel = models.some((model) => model.id === this.state.selectedModelId) ? this.state.selectedModelId : models[0].id;
    const combination = voices.flatMap((voice) => models.map((model) => ({ voiceId: voice.id, modelId: model.id })))
      .find((item) => !used.has(`${item.voiceId}\u0000${item.modelId}`));
    if (!combination) {
      this.toast("没有更多可用的原声与模型组合", true);
      return;
    }
    const id = String(++this.sequence);
    this.state.generationConfigurations.push({
      id,
      voiceId: combination.voiceId || fallbackVoice,
      modelId: combination.modelId || fallbackModel,
      candidateCount: previous?.candidateCount || 1,
    });
    this.storePosition();
    this.render(id);
  }

  remove(id) {
    this.state.generationConfigurations = this.state.generationConfigurations.filter(
      (configuration) => configuration.id !== id,
    );
    this.storePosition();
    this.render();
  }

  updateCombinationCount() {
    const voices = new Set(this.availableVoices().map((voice) => voice.id));
    const models = new Set(this.availableModels().map((model) => model.id));
    const complete = this.state.generationConfigurations.filter(
      (configuration) => voices.has(configuration.voiceId) && models.has(configuration.modelId),
    );
    const candidates = complete.reduce((total, configuration) => total + configuration.candidateCount, 0);
    const incomplete = this.state.generationConfigurations.length - complete.length;
    const lineNumber = this.state.generationRequest?.lineNumber;
    const target = lineNumber === null || lineNumber === undefined ? "整个台本" : `第 ${lineNumber} 行`;
    byId("generationCombinationCount").textContent = this.state.generationConfigurations.length
      ? `${target} · ${this.state.generationConfigurations.length} 条生成配置 · 每行共 ${candidates} 条候选${incomplete ? ` · ${incomplete} 条待完善` : ""}`
      : "请点击“＋ 添加生成”添加至少一条配置";
    byId("generationSubmitButton").disabled = !this.state.generationConfigurations.length || incomplete > 0;
  }
}
