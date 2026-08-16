from __future__ import annotations

import hashlib
import io
import wave
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
import soundfile as sf

from voice_core.pronunciation import (
    is_raw_pronunciation,
    strip_pronunciation_dashes,
)

from ..provider_config import ProviderConfigStore
from .contracts import GenerationResult, ModelStatus, ReferenceAudio


MIN_CLONE_SECONDS = 10
MAX_CLONE_SECONDS = 300
MAX_CLONE_DATA_BYTES = 19 * 1024 * 1024
SAMPLE_GAP_SECONDS = 0.12


class MiniMaxAdapter:
    """MiniMax voice cloning and synchronous TTS adapter.

    MiniMax requires at least ten seconds for a cloned voice.  Voice Lab keeps
    individual source recordings small, so enrollment combines the enabled
    recordings in their stable library order before upload.
    """

    engine_id = "minimax"
    provider_id = "minimax"

    def __init__(self, config: ProviderConfigStore) -> None:
        self._config = config

    @property
    def is_loaded(self) -> bool:
        return False

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        del profile
        config = self._config.provider(self.provider_id)
        if not config.get("enabled"):
            return ModelStatus(False, reason="管理员尚未启用 MiniMax")
        if not str(config.get("api_key") or "").strip():
            return ModelStatus(False, reason="管理员尚未配置 MiniMax API Key")
        return ModelStatus(True)

    def test_connection(self) -> dict[str, Any]:
        config = self._configured_config()
        started = perf_counter()
        response = self._request(config, "连接检测", "GET", "/v1/models")
        self._ensure_http_success(response, "连接检测")
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        models = payload.get("data", []) if isinstance(payload, dict) else []
        return {
            "ok": True,
            "message": "MiniMax API 连接正常",
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
        started = perf_counter()
        voice_id = self._ensure_voice(reference, config)
        text = self._tts_text(item)
        if not text:
            raise RuntimeError("MiniMax 生成文本不能为空")
        voice_setting: dict[str, Any] = {
            "voice_id": voice_id,
            "speed": float(config["speed"]),
            "vol": float(config["volume"]),
            "pitch": int(config["pitch"]),
        }
        emotion = str(config.get("emotion") or "").strip()
        if emotion:
            voice_setting["emotion"] = emotion
        body = {
            "model": str(config["tts_model_id"]),
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": int(config["sample_rate"]),
                "format": str(config["output_format"]),
                "channel": 1,
            },
            "language_boost": str(config["language_boost"]),
            "output_format": "hex",
        }
        response = self._request(
            config,
            "语音生成",
            "POST",
            "/v1/t2a_v2",
            json=body,
        )
        payload = self._success_payload(response, "语音生成")
        encoded = str((payload.get("data") or {}).get("audio") or "")
        try:
            audio = bytes.fromhex(encoded)
        except ValueError as error:
            raise RuntimeError("MiniMax 返回的音频不是有效十六进制数据") from error
        duration = self._validate_wav(audio)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        return GenerationResult(
            output_path,
            duration,
            f"minimax:{config['tts_model_id']}",
            perf_counter() - started,
        )

    def unload(self) -> None:
        return None

    def _ensure_voice(self, reference: ReferenceAudio, config: dict[str, Any]) -> str:
        source_paths = reference.source_paths or (reference.path,)
        fingerprint = self._enrollment_fingerprint(source_paths, config)
        voice_key = reference.voice_id or fingerprint
        cached = self._config.enrollment(self.provider_id, voice_key, fingerprint)
        if cached:
            return cached

        clone_audio, clone_seconds = self._combined_clone_wav(source_paths)
        if clone_seconds < MIN_CLONE_SECONDS:
            raise RuntimeError(
                "MiniMax 音色复刻要求至少 10 秒有效录音；" f"当前启用样本合计 {clone_seconds:.2f} 秒"
            )
        file_id = self._upload_file(
            config,
            purpose="voice_clone",
            filename="voice-lab-clone.wav",
            payload=clone_audio,
        )
        external_voice_id = f"VoiceLab_{fingerprint[:24]}"
        body: dict[str, Any] = {
            "file_id": file_id,
            "voice_id": external_voice_id,
            "model": str(config["tts_model_id"]),
            "need_noise_reduction": bool(config["need_noise_reduction"]),
            "need_volume_normalization": bool(config["need_volume_normalization"]),
        }
        prompt = self._prompt(reference, config)
        if prompt:
            body["clone_prompt"] = prompt
        response = self._request(
            config,
            "小样本声音克隆",
            "POST",
            "/v1/voice_clone",
            json=body,
        )
        self._success_payload(response, "小样本声音克隆")
        self._config.remember_enrollment(
            self.provider_id,
            voice_key,
            fingerprint,
            external_voice_id,
        )
        return external_voice_id

    def _prompt(
        self, reference: ReferenceAudio, config: dict[str, Any]
    ) -> dict[str, Any] | None:
        prompt_text = reference.prompt_text.strip()
        if not prompt_text or reference.duration_seconds >= 8:
            return None
        prompt_audio = reference.path.read_bytes()
        prompt_file_id = self._upload_file(
            config,
            purpose="prompt_audio",
            filename="voice-lab-prompt.wav",
            payload=prompt_audio,
        )
        return {"prompt_audio": prompt_file_id, "prompt_text": prompt_text}

    def _upload_file(
        self,
        config: dict[str, Any],
        *,
        purpose: str,
        filename: str,
        payload: bytes,
    ) -> int:
        response = self._request(
            config,
            "参考音频上传",
            "POST",
            "/v1/files/upload",
            data={"purpose": purpose},
            files={"file": (filename, payload, "audio/wav")},
        )
        result = self._success_payload(response, "参考音频上传")
        try:
            file_id = int((result.get("file") or {}).get("file_id"))
        except (TypeError, ValueError) as error:
            raise RuntimeError("MiniMax 上传响应缺少 file_id") from error
        if file_id <= 0:
            raise RuntimeError("MiniMax 上传响应缺少 file_id")
        return file_id

    def _available_config(self) -> dict[str, Any]:
        status = self.status({})
        if not status.available:
            raise RuntimeError(status.reason)
        return self._config.provider(self.provider_id)

    def _configured_config(self) -> dict[str, Any]:
        config = self._config.provider(self.provider_id)
        if not str(config.get("api_key") or "").strip():
            raise RuntimeError("管理员尚未配置 MiniMax API Key")
        return config

    @staticmethod
    def _client(config: dict[str, Any]) -> httpx.Client:
        return httpx.Client(
            base_url=str(config["base_url"]),
            headers={"Authorization": f"Bearer {config['api_key']}"},
            timeout=float(config["request_timeout_seconds"]),
        )

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
            raise RuntimeError(f"MiniMax {action}超时，请检查管理员超时设置和网络") from error
        except httpx.RequestError as error:
            raise RuntimeError(f"MiniMax {action}网络请求失败，请检查管理员 API 地址和网络") from error

    @classmethod
    def _success_payload(cls, response: httpx.Response, action: str) -> dict[str, Any]:
        cls._ensure_http_success(response, action)
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"MiniMax {action}响应不是有效 JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError(f"MiniMax {action}响应不是 JSON 对象")
        base = payload.get("base_resp") or {}
        try:
            status_code = int(base.get("status_code", 0))
        except (AttributeError, TypeError, ValueError):
            status_code = -1
        if status_code != 0:
            message = (
                str(base.get("status_msg") or "") if isinstance(base, dict) else ""
            )
            suffix = f": {message[:300]}" if message else ""
            raise RuntimeError(f"MiniMax {action}失败（状态码 {status_code}）{suffix}")
        return payload

    @staticmethod
    def _ensure_http_success(response: httpx.Response, action: str) -> None:
        if response.is_success:
            return
        raise RuntimeError(f"MiniMax {action}失败（HTTP {response.status_code}）")

    @staticmethod
    def _validate_wav(payload: bytes) -> float:
        try:
            with sf.SoundFile(io.BytesIO(payload)) as audio:
                if audio.frames <= 0 or audio.samplerate <= 0:
                    raise RuntimeError("MiniMax 返回了空音频")
                return round(audio.frames / audio.samplerate, 3)
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError("MiniMax 未返回有效 WAV，请检查管理员输出格式") from error

    @staticmethod
    def _tts_text(item: dict[str, Any]) -> str:
        text = strip_pronunciation_dashes(str(item["generated_text"])).strip()
        if not is_raw_pronunciation(str(item.get("pronunciation") or "")):
            return text
        body = text.rstrip(".?!。？！").strip()
        ending = text[len(body) :] if body else ""
        # MiniMax documents inline IPA by wrapping it in parentheses.
        return f"({body}){ending}" if body else ""

    @staticmethod
    def _enrollment_fingerprint(paths: tuple[Path, ...], config: dict[str, Any]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(b"\0sample\0")
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        for key in (
            "base_url",
            "api_key",
            "tts_model_id",
            "need_noise_reduction",
            "need_volume_normalization",
        ):
            digest.update(f"\0{key}\0".encode())
            digest.update(str(config.get(key) or "").encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _combined_clone_wav(paths: tuple[Path, ...]) -> tuple[bytes, float]:
        if not paths:
            raise RuntimeError("MiniMax 音色复刻没有可上传的参考录音")
        output = io.BytesIO()
        params: tuple[int, int, int] | None = None
        frames: list[bytes] = []
        source_frames = 0
        stored_bytes = 0
        for path in paths:
            try:
                with wave.open(str(path), "rb") as audio:
                    current = (
                        audio.getnchannels(),
                        audio.getsampwidth(),
                        audio.getframerate(),
                    )
                    if current[0] != 1 or current[1] != 2:
                        raise RuntimeError("MiniMax 参考录音必须是单声道 16-bit WAV")
                    if params is None:
                        params = current
                    elif current != params:
                        raise RuntimeError("MiniMax 参考录音的 WAV 格式必须一致")
                    chunk = audio.readframes(audio.getnframes())
            except (OSError, wave.Error) as error:
                raise RuntimeError(f"MiniMax 无法读取参考录音: {path.name}") from error
            source_frames += len(chunk) // current[1]
            remaining = MAX_CLONE_DATA_BYTES - stored_bytes
            if remaining <= 0:
                break
            chunk = chunk[: remaining - (remaining % current[1])]
            if chunk:
                frames.append(chunk)
                stored_bytes += len(chunk)
            if stored_bytes >= MAX_CLONE_DATA_BYTES:
                break
            gap = b"\0\0" * int(current[2] * SAMPLE_GAP_SECONDS)
            gap = gap[: MAX_CLONE_DATA_BYTES - stored_bytes]
            frames.append(gap)
            stored_bytes += len(gap)
        if params is None:
            raise RuntimeError("MiniMax 音色复刻没有可读取的参考录音")
        sample_rate = params[2]
        source_seconds = source_frames / sample_rate
        if source_seconds > MAX_CLONE_SECONDS:
            source_seconds = MAX_CLONE_SECONDS
        with wave.open(output, "wb") as combined:
            combined.setnchannels(params[0])
            combined.setsampwidth(params[1])
            combined.setframerate(sample_rate)
            combined.writeframes(b"".join(frames))
        return output.getvalue(), source_seconds
