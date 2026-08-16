from __future__ import annotations

import json
import os
import stat
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_ELEVENLABS = {
    "enabled": False,
    "api_key": "",
    "base_url": "https://api.elevenlabs.io",
    "tts_model_id": "eleven_multilingual_v2",
    "output_format": "wav_48000",
    "request_timeout_seconds": 180,
    "remove_background_noise": False,
    "stability": 0.5,
    "similarity_boost": 0.85,
    "style": 0.0,
    "use_speaker_boost": True,
}

DEFAULT_MINIMAX = {
    "enabled": False,
    "api_key": "",
    "base_url": "https://api.minimaxi.com",
    "tts_model_id": "speech-2.8-hd",
    "output_format": "wav",
    "sample_rate": 32000,
    "request_timeout_seconds": 180,
    "language_boost": "auto",
    "speed": 1.0,
    "volume": 1.0,
    "pitch": 0,
    "emotion": "",
    "need_noise_reduction": False,
    "need_volume_normalization": False,
}

_DEFAULT_DATA = {
    "version": 1,
    "providers": {
        "elevenlabs": DEFAULT_ELEVENLABS,
        "minimax": DEFAULT_MINIMAX,
    },
    "enrollments": {"elevenlabs": {}, "minimax": {}},
}


class ProviderConfigStore:
    """Persist administrator-managed provider settings outside source control."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def provider(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            configured = data.get("providers", {}).get(provider_id, {})
            defaults = _DEFAULT_DATA["providers"].get(provider_id, {})
            if not isinstance(configured, dict):
                configured = {}
            if not isinstance(defaults, dict):
                defaults = {}
            return {**defaults, **configured}

    def public(self) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            provider = self.provider("elevenlabs")
            minimax = self.provider("minimax")
            return {
                "providers": {
                    "elevenlabs": {
                        "enabled": bool(provider.get("enabled")),
                        "api_key_configured": bool(
                            str(provider.get("api_key") or "").strip()
                        ),
                        "base_url": str(provider.get("base_url") or ""),
                        "tts_model_id": str(provider.get("tts_model_id") or ""),
                        "output_format": str(provider.get("output_format") or ""),
                        "request_timeout_seconds": int(
                            provider.get("request_timeout_seconds") or 180
                        ),
                        "remove_background_noise": bool(
                            provider.get("remove_background_noise")
                        ),
                        "stability": float(provider.get("stability") or 0),
                        "similarity_boost": float(
                            provider.get("similarity_boost") or 0
                        ),
                        "style": float(provider.get("style") or 0),
                        "use_speaker_boost": bool(provider.get("use_speaker_boost")),
                        "enrolled_voice_count": len(
                            data.get("enrollments", {}).get("elevenlabs", {})
                        ),
                    },
                    "minimax": {
                        "enabled": bool(minimax.get("enabled")),
                        "api_key_configured": bool(
                            str(minimax.get("api_key") or "").strip()
                        ),
                        "base_url": str(minimax.get("base_url") or ""),
                        "tts_model_id": str(minimax.get("tts_model_id") or ""),
                        "output_format": str(minimax.get("output_format") or "wav"),
                        "sample_rate": int(minimax.get("sample_rate") or 32000),
                        "request_timeout_seconds": int(
                            minimax.get("request_timeout_seconds") or 180
                        ),
                        "language_boost": str(minimax.get("language_boost") or "auto"),
                        "speed": float(minimax.get("speed") or 1),
                        "volume": float(minimax.get("volume") or 1),
                        "pitch": int(minimax.get("pitch") or 0),
                        "emotion": str(minimax.get("emotion") or ""),
                        "need_noise_reduction": bool(
                            minimax.get("need_noise_reduction")
                        ),
                        "need_volume_normalization": bool(
                            minimax.get("need_volume_normalization")
                        ),
                        "enrolled_voice_count": len(
                            data.get("enrollments", {}).get("minimax", {})
                        ),
                    },
                }
            }

    def update_provider(self, provider_id: str, changes: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            provider = {
                **_DEFAULT_DATA["providers"].get(provider_id, {}),
                **data.setdefault("providers", {}).get(provider_id, {}),
            }
            provider.update(changes)
            data["providers"][provider_id] = provider
            self._write(data)

    def enrollment(
        self, provider_id: str, voice_key: str, fingerprint: str
    ) -> str | None:
        with self._lock:
            data = self._read()
            entry = data.get("enrollments", {}).get(provider_id, {}).get(voice_key)
            if not isinstance(entry, dict):
                return None
            if str(entry.get("fingerprint") or "") != fingerprint:
                return None
            value = str(entry.get("external_voice_id") or "").strip()
            return value or None

    def remember_enrollment(
        self,
        provider_id: str,
        voice_key: str,
        fingerprint: str,
        external_voice_id: str,
    ) -> None:
        with self._lock:
            data = self._read()
            enrollments = data.setdefault("enrollments", {}).setdefault(provider_id, {})
            enrollments[voice_key] = {
                "fingerprint": fingerprint,
                "external_voice_id": external_voice_id,
            }
            self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self._path.is_file():
            return deepcopy(_DEFAULT_DATA)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"provider 配置文件无法读取: {self._path}") from error
        if not isinstance(payload, dict):
            raise RuntimeError("provider 配置文件必须是 JSON 对象")
        return {
            **deepcopy(_DEFAULT_DATA),
            **payload,
            "providers": {
                **deepcopy(_DEFAULT_DATA["providers"]),
                **(payload.get("providers") or {}),
            },
            "enrollments": {
                **deepcopy(_DEFAULT_DATA["enrollments"]),
                **(payload.get("enrollments") or {}),
            },
        }

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temporary, self._path)
