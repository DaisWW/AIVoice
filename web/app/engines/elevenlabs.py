from __future__ import annotations

import hashlib
import io
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote

import httpx
import soundfile as sf

from voice_core.pronunciation import strip_pronunciation_dashes

from ..provider_config import ProviderConfigStore
from .contracts import GenerationResult, ModelStatus, ReferenceAudio


class ElevenLabsAdapter:
    engine_id = "elevenlabs"
    provider_id = "elevenlabs"

    def __init__(self, config: ProviderConfigStore) -> None:
        self._config = config

    @property
    def is_loaded(self) -> bool:
        return False

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        del profile
        config = self._config.provider(self.provider_id)
        if not config.get("enabled"):
            return ModelStatus(False, reason="管理员尚未启用 ElevenLabs")
        if not str(config.get("api_key") or "").strip():
            return ModelStatus(False, reason="管理员尚未配置 ElevenLabs API Key")
        return ModelStatus(True)

    def test_connection(self) -> dict[str, Any]:
        config = self._configured_config()
        started = perf_counter()
        response = self._request(config, "连接检测", "GET", "/v1/models")
        self._ensure_success(response, "连接检测")
        try:
            models = response.json()
        except ValueError:
            models = []
        return {
            "ok": True,
            "message": "ElevenLabs API 连接正常",
            "model_count": len(models) if isinstance(models, list) else None,
            "elapsed_seconds": round(perf_counter() - started, 3),
        }

    def generate(
        self,
        item: dict[str, Any],
        reference: ReferenceAudio,
        profile: dict[str, Any],
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int],
    ) -> GenerationResult:
        del profile, seed, generation_settings
        config = self._available_config()
        text = strip_pronunciation_dashes(str(item.get("generated_text") or "")).strip()
        if not text:
            raise RuntimeError("ElevenLabs 生成文本不能为空")
        started = perf_counter()
        voice_key, fingerprint = self._enrollment_context(reference, config)
        external_voice_id = self._ensure_voice(reference, config)
        body = {
            "text": text,
            "model_id": str(config["tts_model_id"]),
            "voice_settings": {
                "stability": float(config["stability"]),
                "similarity_boost": float(config["similarity_boost"]),
                "style": float(config["style"]),
                "use_speaker_boost": bool(config["use_speaker_boost"]),
            },
        }
        path = f"/v1/text-to-speech/{quote(external_voice_id, safe='')}"
        response = self._request(
            config,
            "语音生成",
            "POST",
            path,
            params={"output_format": str(config["output_format"])},
            json=body,
        )
        if self._is_missing_voice(response):
            self._config.forget_enrollment(self.provider_id, voice_key)
            external_voice_id = self._ensure_voice(
                reference, config, force_refresh=True
            )
            path = f"/v1/text-to-speech/{quote(external_voice_id, safe='')}"
            response = self._request(
                config,
                "语音生成",
                "POST",
                path,
                params={"output_format": str(config["output_format"])},
                json=body,
            )
        self._ensure_success(response, "语音生成")
        duration = self._validate_wav(response.content)
        self._config.touch_enrollment(self.provider_id, voice_key, fingerprint)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return GenerationResult(
            output_path,
            duration,
            f"elevenlabs:{config['tts_model_id']}",
            perf_counter() - started,
        )

    def unload(self) -> None:
        return None

    def _ensure_voice(
        self,
        reference: ReferenceAudio,
        config: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> str:
        sample_paths = reference.source_paths or (reference.path,)
        voice_key, fingerprint = self._enrollment_context(reference, config)
        cached = (
            None
            if force_refresh
            else self._config.enrollment(self.provider_id, voice_key, fingerprint)
        )
        if cached:
            return cached
        display_name = (reference.voice_name or reference.voice_id or fingerprint[:12])[
            :60
        ]
        data = {
            "name": f"Voice Lab · {display_name}",
            "description": f"Voice Lab 小样本克隆 · {voice_key}",
            "remove_background_noise": str(
                bool(config["remove_background_noise"])
            ).lower(),
        }
        with ExitStack() as stack:
            files = [
                (
                    "files",
                    (path.name, stack.enter_context(path.open("rb")), "audio/wav"),
                )
                for path in sample_paths
            ]
            response = self._request(
                config,
                "小样本声音克隆",
                "POST",
                "/v1/voices/add",
                data=data,
                files=files,
            )
        self._ensure_success(response, "小样本声音克隆")
        try:
            external_voice_id = str(response.json().get("voice_id") or "").strip()
        except (AttributeError, ValueError) as error:
            raise RuntimeError("ElevenLabs 克隆响应缺少 voice_id") from error
        if not external_voice_id:
            raise RuntimeError("ElevenLabs 克隆响应缺少 voice_id")
        self._config.remember_enrollment(
            self.provider_id,
            voice_key,
            fingerprint,
            external_voice_id,
        )
        return external_voice_id

    @staticmethod
    def _enrollment_context(
        reference: ReferenceAudio, config: dict[str, Any]
    ) -> tuple[str, str]:
        sample_paths = reference.source_paths or (reference.path,)
        fingerprint = ElevenLabsAdapter._enrollment_fingerprint(sample_paths, config)
        return reference.voice_id or fingerprint, fingerprint

    @staticmethod
    def _is_missing_voice(response: httpx.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        detail = payload.get("detail", payload)
        if not isinstance(detail, dict):
            return False
        status = str(detail.get("status") or "").strip().lower()
        if status in {
            "voice_not_found",
            "voice_not_found_error",
            "voice_deleted",
            "voice_expired",
            "voice_expired_error",
        }:
            return True
        message = str(detail.get("message") or "").strip().lower()
        return any(
            marker in message
            for marker in (
                "voice was not found",
                "voice could not be found",
                "voice does not exist",
                "voice has been deleted",
                "voice has expired",
                "voice expired",
            )
        )

    def _available_config(self) -> dict[str, Any]:
        status = self.status({})
        if not status.available:
            raise RuntimeError(status.reason)
        return self._config.provider(self.provider_id)

    def _configured_config(self) -> dict[str, Any]:
        config = self._config.provider(self.provider_id)
        if not str(config.get("api_key") or "").strip():
            raise RuntimeError("管理员尚未配置 ElevenLabs API Key")
        return config

    @staticmethod
    def _client(config: dict[str, Any]) -> httpx.Client:
        return httpx.Client(
            base_url=str(config["base_url"]),
            headers={"xi-api-key": str(config["api_key"])},
            timeout=float(config["request_timeout_seconds"]),
        )

    @staticmethod
    def _enrollment_fingerprint(paths: tuple[Path, ...], config: dict[str, Any]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(b"\0sample\0")
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        digest.update(b"\0base-url\0")
        digest.update(str(config.get("base_url") or "").encode("utf-8"))
        digest.update(b"\0api-key\0")
        digest.update(str(config.get("api_key") or "").encode("utf-8"))
        digest.update(b"\0noise-reduction\0")
        digest.update(str(bool(config.get("remove_background_noise"))).encode())
        return digest.hexdigest()

    @classmethod
    def _request(
        cls,
        config: dict[str, Any],
        action: str,
        method: str,
        path: str,
        **options: Any,
    ) -> httpx.Response:
        try:
            with cls._client(config) as client:
                return client.request(method, path, **options)
        except httpx.TimeoutException as error:
            raise RuntimeError(f"ElevenLabs {action}超时，请检查管理员超时设置和网络") from error
        except httpx.InvalidURL as error:
            raise RuntimeError(f"ElevenLabs {action} API 地址无效") from error
        except httpx.RequestError as error:
            raise RuntimeError(f"ElevenLabs {action}网络请求失败，请检查管理员 API 地址和网络") from error
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise RuntimeError(f"ElevenLabs {action}配置无效，请检查管理员设置") from error

    @staticmethod
    def _validate_wav(payload: bytes) -> float:
        try:
            with sf.SoundFile(io.BytesIO(payload)) as audio:
                if audio.frames <= 0 or audio.samplerate <= 0:
                    raise RuntimeError("ElevenLabs 返回了空音频")
                return round(audio.frames / audio.samplerate, 3)
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError("ElevenLabs 未返回有效 WAV，请检查管理员输出格式") from error

    @staticmethod
    def _ensure_success(response: httpx.Response, action: str) -> None:
        if response.is_success:
            return
        raise RuntimeError(f"ElevenLabs {action}失败（HTTP {response.status_code}）")
