import { $, $$, renderSelect, setButtonBusy } from "../core/dom.js";
import { isVoiceUsable, voiceOptionLabel } from "./script-voice-utils.js";

export class ScriptController {
  #state;
  #api;
  #shell;
  #listView;
  #detailView;
  #onScriptsChanged;
  #request = null;

  constructor({ state, api, shell, listView, detailView, onScriptsChanged }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
    this.#listView = listView;
    this.#detailView = detailView;
    this.#onScriptsChanged = onScriptsChanged;
  }

  bind() {
    $("#scriptList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-script-id]");
      if (button) this.select(button.dataset.scriptId);
    });
    $("#scriptDetail").addEventListener("submit", (event) => {
      if (event.target.id === "scriptItemsForm") {
        event.preventDefault();
        this.#saveItems(event.target);
        return;
      }
      if (event.target.id !== "scriptEditForm") return;
      event.preventDefault();
      this.#edit(event.target);
    });
    $("#scriptDetail").addEventListener("click", (event) => {
      const control = event.target.closest("[data-script-action]");
      if (control?.dataset.scriptAction === "delete") this.#delete();
    });
    $("#scriptDetail").addEventListener("change", (event) => {
      if (event.target.id === "scriptImportFile" && event.target.files[0]) {
        this.#import(event.target.files[0], event.target);
      }
    });
    $("#createScript").addEventListener("click", () => this.#openCreate());
    $("#scriptCreateForm").addEventListener("submit", (event) => this.#create(event));
    $("#scriptUploadFile").addEventListener("change", (event) => {
      $("#scriptUploadFileName").textContent =
        event.target.files[0]?.name || "选择台本文件";
    });
  }

  render() {
    this.#renderCreateVoiceOptions();
    this.#listView.render();
    if (this.#state.scriptDetail?.id === this.#state.selectedScriptId) {
      this.#detailView.render(this.#state.scriptDetail);
    } else if (!this.#state.scripts.length) {
      this.#detailView.clear();
    }
  }

  async ensureSelection() {
    if (!this.#state.scripts.length) {
      this.#state.selectScript(null);
      this.#state.scriptDetail = null;
      this.#cancelSelection();
      this.#detailView.clear();
      return;
    }
    const selected = this.#state.scripts.find(
      (script) => script.id === this.#state.selectedScriptId,
    );
    await this.select((selected || this.#state.scripts[0]).id);
  }

  async select(scriptId) {
    const previousScriptId = this.#state.scriptDetail?.id || null;
    this.#state.selectScript(scriptId);
    this.#listView.render();
    if (previousScriptId === scriptId && this.#detailView.hasScript(scriptId)) {
      this.#cancelSelection();
      this.#detailView.setLoading(false);
      return;
    }
    await this.#loadSelection(scriptId, previousScriptId);
  }

  async #loadSelection(scriptId, previousScriptId) {
    this.#cancelSelection();
    const controller = new AbortController();
    this.#request = controller;
    this.#detailView.setLoading(true);
    this.#detailView.showInitialLoading();
    try {
      const { script } = await this.#api.get(
        `/api/scripts/${encodeURIComponent(scriptId)}`,
        { signal: controller.signal },
      );
      if (this.#state.selectedScriptId === scriptId) this.#sync(script);
    } catch (error) {
      this.#handleSelectionError(error, scriptId, previousScriptId);
    } finally {
      this.#finishSelection(controller, scriptId, previousScriptId);
    }
  }

  #handleSelectionError(error, scriptId, previousScriptId) {
    if (error.name === "AbortError") return;
    if (this.#state.selectedScriptId === scriptId) {
      this.#state.selectScript(previousScriptId);
      this.#listView.render();
      if (!previousScriptId) this.#detailView.clear();
    }
    this.#shell.toast(error.message, true);
  }

  #finishSelection(controller, scriptId, previousScriptId) {
    if (this.#request !== controller) return;
    this.#request = null;
    if (
      this.#state.selectedScriptId === scriptId
      || this.#state.selectedScriptId === previousScriptId
    ) {
      this.#detailView.setLoading(false);
    }
  }

  #cancelSelection() {
    this.#request?.abort();
  }

  #renderCreateVoiceOptions() {
    const voices = this.#state.voices.map((voice) => ({
      ...voice,
      disabled: !isVoiceUsable(voice),
    }));
    renderSelect(
      $("#scriptVoice"),
      voices,
      "选择声音库",
      (voice) => `${voiceOptionLabel(voice)}${voice.disabled ? " · 不可用" : ""}`,
    );
  }

  #sync(script) {
    this.#storeSummary(script);
    this.#state.selectScript(script.id);
    this.#state.scriptDetail = script;
    this.render();
    this.#onScriptsChanged();
  }

  #storeSummary(script) {
    const { items: _, ...summary } = script;
    const index = this.#state.scripts.findIndex((item) => item.id === script.id);
    if (index >= 0) {
      this.#state.scripts[index] = { ...this.#state.scripts[index], ...summary };
    } else {
      this.#state.scripts.unshift(summary);
    }
  }

  #openCreate() {
    if (!this.#state.voices.some((voice) => isVoiceUsable(voice))) {
      this.#shell.toast("请先创建包含启用录音的声音库", true);
      this.#shell.showView("library");
      return;
    }
    this.#renderCreateVoiceOptions();
    $("#scriptDialog").showModal();
    $("#scriptVoice").focus();
  }

  async #edit(form) {
    const scriptId = this.#state.selectedScriptId;
    const projectId = this.#state.projectId;
    if (!scriptId) return;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    try {
      const { script } = await this.#api.patch(
        `/api/scripts/${encodeURIComponent(scriptId)}`,
        {
          name: $("#scriptEditName").value,
          default_voice_id: $("#scriptDefaultVoice").value,
        },
      );
      if (this.#state.projectId !== projectId) return;
      this.#storeSummary(script);
      if (this.#state.scriptDetail?.id === scriptId) {
        this.#state.scriptDetail = { ...this.#state.scriptDetail, ...script };
      }
      this.render();
      this.#onScriptsChanged();
      this.#shell.toast("台本配置已保存");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #create(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button[type='submit']", form);
    setButtonBusy(button, true);
    button.textContent = "正在上传";
    try {
      const data = new FormData(form);
      data.set("project_id", this.#state.projectId || "");
      const { script } = await this.#api.postForm("/api/scripts", data);
      form.reset();
      $("#scriptUploadFileName").textContent = "选择台本文件";
      $("#scriptDialog").close();
      this.#storeSummary(script);
      this.#onScriptsChanged();
      await this.select(script.id);
      this.#shell.toast("台本已上传并配置声音");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
      button.textContent = "上传并保存";
    }
  }

  async #saveItems(form) {
    const scriptId = this.#state.selectedScriptId;
    if (!scriptId) return;
    const button = $("button[type='submit']", form);
    const items = $$('[data-script-item]', form).map((row) => ({
      text: $("textarea[name='text']", row).value,
      pronunciation: $("textarea[name='pronunciation']", row).value,
    }));
    setButtonBusy(button, true);
    try {
      const { script } = await this.#api.put(
        `/api/scripts/${encodeURIComponent(scriptId)}/items`,
        { items },
      );
      this.#sync(script);
      this.#shell.toast("台词与发音已保存");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #import(file, input) {
    const scriptId = this.#state.selectedScriptId;
    if (!scriptId) return;
    const data = new FormData();
    data.append("file", file);
    input.disabled = true;
    try {
      const { script } = await this.#api.postForm(
        `/api/scripts/${encodeURIComponent(scriptId)}/import`,
        data,
      );
      this.#sync(script);
      this.#shell.toast("台本已重新导入");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      input.value = "";
      input.disabled = false;
    }
  }

  async #delete() {
    const scriptId = this.#state.selectedScriptId;
    const script = this.#state.scripts.find((item) => item.id === scriptId);
    if (!script || !window.confirm(`确定删除台本“${script.name}”吗？`)) return;
    try {
      await this.#api.delete(`/api/scripts/${encodeURIComponent(scriptId)}`);
      this.#state.scripts = this.#state.scripts.filter((item) => item.id !== scriptId);
      this.#state.scriptDetail = null;
      this.#state.selectScript(null);
      this.#detailView.clear();
      this.render();
      this.#onScriptsChanged();
      await this.ensureSelection();
      this.#shell.toast("台本已删除");
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
  }
}
