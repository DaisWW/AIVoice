import { $, escapeHtml, setButtonBusy } from "../core/dom.js";

export class ProviderController {
  #state;
  #api;
  #shell;
  #loaded = false;

  constructor({ state, api, shell }) {
    this.#state = state;
    this.#api = api;
    this.#shell = shell;
  }

  bind() {
    $("#elevenLabsSettingsForm").addEventListener("submit", (event) => {
      event.preventDefault();
      this.#saveElevenLabs();
    });
    $("#testElevenLabs").addEventListener("click", () => this.#testElevenLabs());
    $("#miniMaxSettingsForm").addEventListener("submit", (event) => {
      event.preventDefault();
      this.#saveMiniMax();
    });
    $("#testMiniMax").addEventListener("click", () => this.#testMiniMax());
  }

  async ensureLoaded() {
    if (!this.#state.isAdmin || this.#loaded) return;
    try {
      const payload = await this.#api.get("/api/admin/providers");
      this.#renderElevenLabs(payload.providers.elevenlabs);
      this.#renderMiniMax(payload.providers.minimax);
      this.#loaded = true;
    } catch (error) {
      this.#shell.toast(error.message, true);
    }
  }

  renderLocalModels() {
    const models = (this.#state.config.models || []).filter(
      (model) => !["elevenlabs", "minimax"].includes(model.engine),
    );
    $("#localModelStatus").innerHTML = models.map((model) => `
      <div class="local-model-row">
        <div><strong>${escapeHtml(model.label)}</strong><span>${escapeHtml(model.description || "")}</span></div>
        <em class="${model.available ? "available" : "unavailable"}">${model.available ? "可用" : escapeHtml(model.availability_reason || "未安装")}</em>
      </div>
    `).join("");
  }

  async #saveElevenLabs() {
    const button = $("#saveElevenLabs");
    setButtonBusy(button, true);
    try {
      const payload = await this.#api.patch(
        "/api/admin/providers/elevenlabs",
        this.#elevenLabsPayload(),
      );
      this.#renderElevenLabs(payload.providers.elevenlabs);
      this.#loaded = true;
      this.#shell.toast("ElevenLabs 配置已保存");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #testElevenLabs() {
    const button = $("#testElevenLabs");
    setButtonBusy(button, true);
    try {
      const result = await this.#api.post("/api/admin/providers/elevenlabs/test", {});
      this.#shell.toast(`${result.message}（${result.elapsed_seconds}s）`);
      $("#elevenLabsStatus").textContent = "连接正常";
      $("#elevenLabsStatus").className = "provider-status available";
    } catch (error) {
      this.#shell.toast(error.message, true);
      $("#elevenLabsStatus").textContent = "连接失败";
      $("#elevenLabsStatus").className = "provider-status unavailable";
    } finally {
      setButtonBusy(button, false);
    }
  }

  #elevenLabsPayload() {
    return {
      enabled: $("#elevenLabsEnabled").checked,
      api_key: $("#elevenLabsApiKey").value || null,
      clear_api_key: $("#elevenLabsClearKey").checked,
      base_url: $("#elevenLabsBaseUrl").value,
      tts_model_id: $("#elevenLabsModel").value,
      output_format: $("#elevenLabsOutput").value,
      request_timeout_seconds: Number($("#elevenLabsTimeout").value),
      remove_background_noise: $("#elevenLabsNoiseReduction").checked,
      stability: Number($("#elevenLabsStability").value),
      similarity_boost: Number($("#elevenLabsSimilarity").value),
      style: Number($("#elevenLabsStyle").value),
      use_speaker_boost: $("#elevenLabsSpeakerBoost").checked,
    };
  }

  #renderElevenLabs(config) {
    $("#elevenLabsEnabled").checked = config.enabled;
    $("#elevenLabsApiKey").value = "";
    $("#elevenLabsApiKey").placeholder = config.api_key_configured
      ? "已保存；留空则保持不变"
      : "尚未配置";
    $("#elevenLabsClearKey").checked = false;
    $("#elevenLabsBaseUrl").value = config.base_url;
    $("#elevenLabsModel").value = config.tts_model_id;
    $("#elevenLabsOutput").value = config.output_format;
    $("#elevenLabsTimeout").value = config.request_timeout_seconds;
    $("#elevenLabsNoiseReduction").checked = config.remove_background_noise;
    $("#elevenLabsStability").value = config.stability;
    $("#elevenLabsSimilarity").value = config.similarity_boost;
    $("#elevenLabsStyle").value = config.style;
    $("#elevenLabsSpeakerBoost").checked = config.use_speaker_boost;
    $("#elevenLabsStatus").textContent = config.enabled
      ? config.api_key_configured ? "已启用" : "缺少密钥"
      : "已停用";
    $("#elevenLabsStatus").className = `provider-status ${config.enabled && config.api_key_configured ? "available" : ""}`;
    $("#elevenLabsEnrollmentSummary").textContent = config.enrolled_voice_count
      ? `本机已缓存 ${config.enrolled_voice_count} 个云端克隆音色映射；参考录音变化后会自动重新注册。`
      : "首次使用某个声音库时会自动注册小样本音色，并缓存 voice_id。";
  }

  async #saveMiniMax() {
    const button = $("#saveMiniMax");
    setButtonBusy(button, true);
    try {
      const payload = await this.#api.patch(
        "/api/admin/providers/minimax",
        this.#miniMaxPayload(),
      );
      this.#renderMiniMax(payload.providers.minimax);
      this.#loaded = true;
      this.#shell.toast("MiniMax 配置已保存");
    } catch (error) {
      this.#shell.toast(error.message, true);
    } finally {
      setButtonBusy(button, false);
    }
  }

  async #testMiniMax() {
    const button = $("#testMiniMax");
    setButtonBusy(button, true);
    try {
      const result = await this.#api.post("/api/admin/providers/minimax/test", {});
      this.#shell.toast(`${result.message}（${result.elapsed_seconds}s）`);
      $("#miniMaxStatus").textContent = "连接正常";
      $("#miniMaxStatus").className = "provider-status available";
    } catch (error) {
      this.#shell.toast(error.message, true);
      $("#miniMaxStatus").textContent = "连接失败";
      $("#miniMaxStatus").className = "provider-status unavailable";
    } finally {
      setButtonBusy(button, false);
    }
  }

  #miniMaxPayload() {
    return {
      enabled: $("#miniMaxEnabled").checked,
      api_key: $("#miniMaxApiKey").value || null,
      clear_api_key: $("#miniMaxClearKey").checked,
      base_url: $("#miniMaxBaseUrl").value,
      tts_model_id: $("#miniMaxModel").value,
      output_format: "wav",
      sample_rate: Number($("#miniMaxSampleRate").value),
      request_timeout_seconds: Number($("#miniMaxTimeout").value),
      language_boost: $("#miniMaxLanguageBoost").value,
      speed: Number($("#miniMaxSpeed").value),
      volume: Number($("#miniMaxVolume").value),
      pitch: Number($("#miniMaxPitch").value),
      emotion: $("#miniMaxEmotion").value,
      need_noise_reduction: $("#miniMaxNoiseReduction").checked,
      need_volume_normalization: $("#miniMaxVolumeNormalization").checked,
    };
  }

  #renderMiniMax(config) {
    $("#miniMaxEnabled").checked = config.enabled;
    $("#miniMaxApiKey").value = "";
    $("#miniMaxApiKey").placeholder = config.api_key_configured
      ? "已保存；留空则保持不变"
      : "尚未配置";
    $("#miniMaxClearKey").checked = false;
    $("#miniMaxBaseUrl").value = config.base_url;
    $("#miniMaxModel").value = config.tts_model_id;
    $("#miniMaxSampleRate").value = String(config.sample_rate);
    $("#miniMaxTimeout").value = config.request_timeout_seconds;
    $("#miniMaxLanguageBoost").value = config.language_boost;
    $("#miniMaxSpeed").value = config.speed;
    $("#miniMaxVolume").value = config.volume;
    $("#miniMaxPitch").value = config.pitch;
    $("#miniMaxEmotion").value = config.emotion;
    $("#miniMaxNoiseReduction").checked = config.need_noise_reduction;
    $("#miniMaxVolumeNormalization").checked = config.need_volume_normalization;
    $("#miniMaxStatus").textContent = config.enabled
      ? config.api_key_configured ? "已启用" : "缺少密钥"
      : "已停用";
    $("#miniMaxStatus").className = `provider-status ${config.enabled && config.api_key_configured ? "available" : ""}`;
    $("#miniMaxEnrollmentSummary").textContent = config.enrolled_voice_count
      ? `本机已缓存 ${config.enrolled_voice_count} 个 MiniMax 克隆音色映射；参考录音变化后会自动重新注册。`
      : "首次使用某个声音库时会合并至少 10 秒启用录音并注册 voice_id。";
  }
}
