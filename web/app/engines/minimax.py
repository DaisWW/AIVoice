from __future__ import annotations

import hashlib
import io
import secrets
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
WAV_HEADER_BYTES = 44
SAMPLE_GAP_SECONDS = 0.12


class MiniMaxAdapter:
    """MiniMax voice cloning and synchronous TTS adapter.

    MiniMax requires at least ten seconds for a cloned voice.  Voice Lab keeps
    individual source recordings small, so enrollment combines the enabled
    recordings in their stable library order before upload.
    """

    engine_id = "minimax"
    provider_id = "minimax"
    enrollment_max_age_seconds = 7 * 24 * 60 * 60

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
        payload = self._success_payload(response, "连接检测")
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
        text = self._tts_text(item)
        if not text:
            raise RuntimeError("MiniMax 生成文本不能为空")
        started = perf_counter()
        voice_key, fingerprint = self._enrollment_context(reference, config)
        voice_id = self._ensure_voice(reference, config)
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
        if self._is_missing_voice(response):
            self._config.forget_enrollment(self.provider_id, voice_key)
            voice_id = self._ensure_voice(reference, config, force_refresh=True)
            body["voice_setting"]["voice_id"] = voice_id
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
        self._config.touch_enrollment(self.provider_id, voice_key, fingerprint)
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

    def _ensure_voice(
        self,
        reference: ReferenceAudio,
        config: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> str:
        source_paths = reference.source_paths or (reference.path,)
        voice_key, fingerprint = self._enrollment_context(reference, config)
        cached = (
            None
            if force_refresh
            else self._config.enrollment(
                self.provider_id,
                voice_key,
                fingerprint,
                max_age_seconds=self.enrollment_max_age_seconds,
            )
        )
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
        if force_refresh:
            external_voice_id += f"_{secrets.token_hex(4)}"
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

    def _enrollment_context(
        self, reference: ReferenceAudio, config: dict[str, Any]
    ) -> tuple[str, str]:
        source_paths = reference.source_paths or (reference.path,)
        prompt_text = (
            reference.prompt_text.strip() if reference.duration_seconds < 8 else ""
        )
        fingerprint = self._enrollment_fingerprint(
            source_paths,
            config,
            prompt_text,
            reference.path if prompt_text else None,
        )
        return reference.voice_id or fingerprint, fingerprint

    @staticmethod
    def _is_missing_voice(response: httpx.Response) -> bool:
        try:
            body = response.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False
        details: list[str] = []
        for value in body.values():
            if isinstance(value, dict):
                for key in ("code", "status", "status_code", "status_msg", "message"):
                    item = value.get(key)
                    if item is not None:
                        details.append(str(item))
            elif isinstance(value, str):
                details.append(value)
        message = " ".join(details).lower()
        return any(
            marker in message
            for marker in (
                "voice_not_found",
                "voice-not-found",
                "voice not found",
                "voice does not exist",
                "voice_id not found",
                "voice_id does not exist",
                "voice_expired",
                "voice-expired",
                "voice expired",
                "voice has expired",
                "音色不存在",
                "音色已过期",
            )
        )

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
        except httpx.InvalidURL as error:
            raise RuntimeError(f"MiniMax {action} API 地址无效") from error
        except httpx.RequestError as error:
            raise RuntimeError(f"MiniMax {action}网络请求失败，请检查管理员 API 地址和网络") from error
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise RuntimeError(f"MiniMax {action}配置无效，请检查管理员设置") from error

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
            raise RuntimeError(f"MiniMax {action}失败（状态码 {status_code}）")
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
    def _enrollment_fingerprint(
        paths: tuple[Path, ...],
        config: dict[str, Any],
        prompt_text: str = "",
        prompt_path: Path | None = None,
    ) -> str:
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
        if prompt_text.strip() or prompt_path is not None:
            digest.update(b"\0prompt-text\0")
            digest.update(prompt_text.strip().encode("utf-8"))
        if prompt_path is not None:
            digest.update(b"\0prompt-audio\0")
            with prompt_path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _combined_clone_wav(paths: tuple[Path, ...]) -> tuple[bytes, float]:
        if not paths:
            raise RuntimeError("MiniMax 音色复刻没有可上传的参考录音")
        output = io.BytesIO()
        params: tuple[int, int, int] | None = None
        stored_bytes = 0
        data_budget = max(0, MAX_CLONE_DATA_BYTES - WAV_HEADER_BYTES)
        combined_frames = 0
        included_source_frames = 0
        writer: wave.Wave_write | None = None
        try:
            for path in paths:
                try:
                    with wave.open(str(path), "rb") as audio:
                        current = (
                            audio.getnchannels(),
                            audio.getsampwidth(),
                            audio.getframerate(),
                        )
                        if current[0] != 1 or current[1] != 2 or current[2] <= 0:
                            raise RuntimeError("MiniMax 参考录音必须是单声道 16-bit WAV")
                        if params is None:
                            params = current
                            writer = wave.open(output, "wb")
                            writer.setnchannels(current[0])
                            writer.setsampwidth(current[1])
                            writer.setframerate(current[2])
                        elif current != params:
                            raise RuntimeError("MiniMax 参考录音的 WAV 格式必须一致")
                        source_frames = audio.getnframes()
                        if source_frames <= 0:
                            continue
                        bytes_per_frame = current[0] * current[1]
                        max_total_frames = MAX_CLONE_SECONDS * current[2]
                        if stored_bytes >= data_budget:
                            break
                        gap_frames = (
                            int(current[2] * SAMPLE_GAP_SECONDS)
                            if included_source_frames
                            else 0
                        )
                        remaining_bytes = data_budget - stored_bytes
                        remaining_frames = max_total_frames - combined_frames
                        gap_frames = min(
                            gap_frames,
                            max(0, remaining_frames),
                            remaining_bytes // bytes_per_frame,
                        )
                        max_frames = min(
                            source_frames,
                            max(0, remaining_frames - gap_frames),
                            max(0, (remaining_bytes // bytes_per_frame) - gap_frames),
                        )
                        if max_frames <= 0:
                            break
                        if gap_frames > 0:
                            assert writer is not None
                            writer.writeframesraw(
                                b"\0" * (gap_frames * bytes_per_frame)
                            )
                            stored_bytes += gap_frames * bytes_per_frame
                            combined_frames += gap_frames
                        remaining_frames = max_frames
                        while remaining_frames:
                            block = audio.readframes(min(65_536, remaining_frames))
                            if not block:
                                break
                            block_frames = len(block) // bytes_per_frame
                            if block_frames <= 0:
                                break
                            assert writer is not None
                            writer.writeframesraw(
                                block[: block_frames * bytes_per_frame]
                            )
                            stored_bytes += block_frames * bytes_per_frame
                            combined_frames += block_frames
                            included_source_frames += block_frames
                            remaining_frames -= block_frames
                except (OSError, wave.Error) as error:
                    raise RuntimeError(f"MiniMax 无法读取参考录音: {path.name}") from error
            if params is None or writer is None:
                raise RuntimeError("MiniMax 音色复刻没有可读取的参考录音")
        finally:
            if writer is not None:
                writer.close()
        sample_rate = params[2]
        source_seconds = included_source_frames / sample_rate
        return output.getvalue(), source_seconds
