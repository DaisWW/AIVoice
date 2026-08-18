import { $, escapeHtml, setPanelLoading } from "../core/dom.js";
import { formatDate } from "../core/formatters.js";
import { isVoiceUsable, voiceOptionLabel } from "./script-voice-utils.js";

export class ScriptDetailView {
  #state;
  #root;

  constructor(state) {
    this.#state = state;
    this.#root = $("#scriptDetail");
  }

  hasScript(scriptId) {
    return this.#root.dataset.scriptId === scriptId
      && Boolean($(".script-detail-content", this.#root));
  }

  setLoading(loading) {
    setPanelLoading(this.#root, loading);
  }

  showInitialLoading() {
    if ($(".script-detail-content", this.#root)) return;
    this.#root.innerHTML =
      '<div class="stage-empty compact"><span class="empty-number">··</span><h2>读取台本</h2></div>';
  }

  clear() {
    delete this.#root.dataset.scriptId;
    this.#root.innerHTML =
      '<div class="stage-empty compact"><span class="empty-number">台本</span><h2>选择一份台本</h2></div>';
  }

  render(script) {
    const configuredVoice = this.#state.voices.find(
      (voice) => voice.id === script.default_voice_id,
    );
    const usable = isVoiceUsable(configuredVoice);
    const status = usable ? "已配置声音" : configuredVoice ? "声音不可用" : "待配置";
    this.#root.innerHTML = `
      <div class="voice-detail-content script-detail-content">
        <header class="voice-detail-header">
          <div><span class="section-kicker">SCRIPT ASSET</span><h2>${escapeHtml(script.name)}</h2></div>
          <div class="script-detail-actions">
            <a class="download-button" href="/api/scripts/${encodeURIComponent(script.id)}/export" download>导出 CSV</a>
            <label class="secondary-button script-import-button" for="scriptImportFile">导入 CSV</label>
            <input id="scriptImportFile" type="file" accept=".txt,.md,.csv,.docx" hidden>
            <button class="quiet-button danger-button" type="button" data-script-action="delete">删除台本</button>
          </div>
          <span class="script-status-badge${usable ? "" : " missing"}">${status}</span>
        </header>
        ${this.#formMarkup(script)}
        <section class="script-items-section">
          <div class="script-items-toolbar">
            <div><h3>台本内容</h3><p>逐句编辑正文和发音，保存后可直接用于生成</p></div>
            <span>${escapeHtml(formatDate(script.created_at))}</span>
          </div>
          <form id="scriptItemsForm">
            <div class="script-items-list">${this.#itemsMarkup(script.items)}</div>
            <div class="script-items-actions"><button class="primary-button" type="submit">保存台词与发音</button></div>
          </form>
        </section>
      </div>
    `;
    this.#root.dataset.scriptId = script.id;
  }

  #formMarkup(script) {
    return `
      <form id="scriptEditForm" class="voice-form script-form">
        <div><label class="field-label" for="scriptEditName">名称</label><input id="scriptEditName" type="text" maxlength="80" value="${escapeHtml(script.name)}" required></div>
        <div><label class="field-label" for="scriptDefaultVoice">使用声音</label><select id="scriptDefaultVoice" required>${this.#voiceOptions(script.default_voice_id)}</select></div>
        <div><label class="field-label">源文件</label><input type="text" value="${escapeHtml(script.original_name)}" disabled></div>
        <div><label class="field-label">台词段数</label><input type="text" value="${Number(script.item_count || 0)} 段" disabled></div>
        <div class="voice-form-actions"><button class="primary-button" type="submit">保存配置</button></div>
      </form>
    `;
  }

  #voiceOptions(selectedId) {
    return [
      '<option value="">选择声音库</option>',
      ...this.#state.voices.map((voice) => {
        const usable = isVoiceUsable(voice);
        const label = `${voiceOptionLabel(voice)}${usable ? "" : " · 不可用"}`;
        return `
          <option value="${escapeHtml(voice.id)}"${voice.id === selectedId ? " selected" : ""}${usable ? "" : " disabled"}>
            ${escapeHtml(label)}
          </option>
        `;
      }),
    ].join("");
  }

  #itemsMarkup(items = []) {
    if (!items.length) return '<div class="list-empty">台本没有可展示的段落</div>';
    return items.map((item) => `
      <article class="script-item" data-script-item>
        <span>${escapeHtml(String(item.order).padStart(2, "0"))}</span>
        <div class="script-item-fields">
          <label><span>正常台词</span><textarea name="text" rows="2" maxlength="2000" required>${escapeHtml(item.text)}</textarea></label>
          <label><span>发音标记</span><textarea name="pronunciation" rows="2" maxlength="2000" required>${escapeHtml(item.pronunciation)}</textarea></label>
        </div>
      </article>
    `).join("");
  }
}
