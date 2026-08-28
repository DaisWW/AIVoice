from __future__ import annotations

import json
import math
import os
import stat
import threading
from datetime import UTC, datetime, timedelta
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


def _safe_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed


def _safe_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


def _enrollment_count(data: dict[str, Any], provider_id: str) -> int:
    enrollments = data.get("enrollments", {})
    if not isinstance(enrollments, dict):
        return 0
    entries = enrollments.get(provider_id, {})
    return len(entries) if isinstance(entries, dict) else 0


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
            result = {**defaults, **configured}
            result["enabled"] = _safe_bool(result.get("enabled"))
            result["request_timeout_seconds"] = _safe_int(
                result.get("request_timeout_seconds"),
                int(defaults.get("request_timeout_seconds", 180)),
            )
            if not 10 <= result["request_timeout_seconds"] <= 600:
                result["request_timeout_seconds"] = int(
                    defaults.get("request_timeout_seconds", 180)
                )
            if provider_id == "elevenlabs":
                for key in ("stability", "similarity_boost", "style"):
                    result[key] = _safe_float(result.get(key), float(defaults[key]))
                    if not 0 <= result[key] <= 1:
                        result[key] = float(defaults[key])
                result["use_speaker_boost"] = _safe_bool(
                    result.get("use_speaker_boost"),
                )
                result["remove_background_noise"] = _safe_bool(
                    result.get("remove_background_noise"),
                )
            elif provider_id == "minimax":
                result["sample_rate"] = _safe_int(
                    result.get("sample_rate"), int(defaults["sample_rate"])
                )
                if result["sample_rate"] not in {
                    8000,
                    16000,
                    22050,
                    24000,
                    32000,
                    44100,
                }:
                    result["sample_rate"] = int(defaults["sample_rate"])
                result["speed"] = _safe_float(
                    result.get("speed"), float(defaults["speed"])
                )
                if not 0.5 <= result["speed"] <= 2:
                    result["speed"] = float(defaults["speed"])
                result["volume"] = _safe_float(
                    result.get("volume"), float(defaults["volume"])
                )
                if not 0 < result["volume"] <= 10:
                    result["volume"] = float(defaults["volume"])
                result["pitch"] = _safe_int(result.get("pitch"), int(defaults["pitch"]))
                if not -12 <= result["pitch"] <= 12:
                    result["pitch"] = int(defaults["pitch"])
                result["need_noise_reduction"] = _safe_bool(
                    result.get("need_noise_reduction"),
                )
                result["need_volume_normalization"] = _safe_bool(
                    result.get("need_volume_normalization"),
                )
            return result

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
                        "request_timeout_seconds": _safe_int(
                            provider.get("request_timeout_seconds") or 180, 180
                        ),
                        "remove_background_noise": bool(
                            provider.get("remove_background_noise")
                        ),
                        "stability": _safe_float(provider.get("stability") or 0, 0),
                        "similarity_boost": _safe_float(
                            provider.get("similarity_boost") or 0, 0
                        ),
                        "style": _safe_float(provider.get("style") or 0, 0),
                        "use_speaker_boost": bool(provider.get("use_speaker_boost")),
                        "enrolled_voice_count": _enrollment_count(data, "elevenlabs"),
                    },
                    "minimax": {
                        "enabled": bool(minimax.get("enabled")),
                        "api_key_configured": bool(
                            str(minimax.get("api_key") or "").strip()
                        ),
                        "base_url": str(minimax.get("base_url") or ""),
                        "tts_model_id": str(minimax.get("tts_model_id") or ""),
                        "output_format": str(minimax.get("output_format") or "wav"),
                        "sample_rate": _safe_int(
                            minimax.get("sample_rate") or 32000, 32000
                        ),
                        "request_timeout_seconds": _safe_int(
                            minimax.get("request_timeout_seconds") or 180, 180
                        ),
                        "language_boost": str(minimax.get("language_boost") or "auto"),
                        "speed": _safe_float(minimax.get("speed") or 1, 1),
                        "volume": _safe_float(minimax.get("volume") or 1, 1),
                        "pitch": _safe_int(minimax.get("pitch") or 0, 0),
                        "emotion": str(minimax.get("emotion") or ""),
                        "need_noise_reduction": bool(
                            minimax.get("need_noise_reduction")
                        ),
                        "need_volume_normalization": bool(
                            minimax.get("need_volume_normalization")
                        ),
                        "enrolled_voice_count": _enrollment_count(data, "minimax"),
                    },
                }
            }

    def update_provider(self, provider_id: str, changes: dict[str, Any]) -> None:
        with self._lock:
            try:
                data = self._read()
            except RuntimeError:
                # Let a complete admin payload replace a truncated config. Any
                # secret in an unreadable file is intentionally not recovered.
                data = deepcopy(_DEFAULT_DATA)
            provider = {
                **_DEFAULT_DATA["providers"].get(provider_id, {}),
                **data.setdefault("providers", {}).get(provider_id, {}),
            }
            provider.update(changes)
            data["providers"][provider_id] = provider
            self._write(data)

    def enrollment(
        self,
        provider_id: str,
        voice_key: str,
        fingerprint: str,
        *,
        max_age_seconds: int | None = None,
    ) -> str | None:
        with self._lock:
            data = self._read()
            entry = data.get("enrollments", {}).get(provider_id, {}).get(voice_key)
            if not isinstance(entry, dict):
                return None
            if str(entry.get("fingerprint") or "") != fingerprint:
                return None
            if max_age_seconds is not None:
                created_at = str(
                    entry.get("last_used_at") or entry.get("created_at") or ""
                )
                try:
                    created = datetime.fromisoformat(created_at)
                except (TypeError, ValueError):
                    return None
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                if datetime.now(UTC) - created > timedelta(seconds=max_age_seconds):
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
            now = datetime.now(UTC).isoformat(timespec="seconds")
            enrollments[voice_key] = {
                "fingerprint": fingerprint,
                "external_voice_id": external_voice_id,
                "created_at": now,
                "last_used_at": now,
            }
            self._write(data)

    def touch_enrollment(
        self, provider_id: str, voice_key: str, fingerprint: str
    ) -> bool:
        """Refresh a cached cloud voice after a successful formal TTS call."""
        with self._lock:
            data = self._read()
            enrollments = data.setdefault("enrollments", {}).setdefault(provider_id, {})
            entry = enrollments.get(voice_key)
            if (
                not isinstance(entry, dict)
                or str(entry.get("fingerprint") or "") != fingerprint
            ):
                return False
            if not str(entry.get("external_voice_id") or "").strip():
                return False
            entry["last_used_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            self._write(data)
            return True

    def forget_enrollment(self, provider_id: str, voice_key: str) -> None:
        with self._lock:
            data = self._read()
            enrollments = data.setdefault("enrollments", {}).setdefault(provider_id, {})
            if voice_key in enrollments:
                del enrollments[voice_key]
                self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self._path.is_file():
            return deepcopy(_DEFAULT_DATA)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("provider 配置文件无法读取") from error
        if not isinstance(payload, dict):
            raise RuntimeError("provider 配置文件必须是 JSON 对象")
        providers = payload.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        enrollments = payload.get("enrollments")
        if not isinstance(enrollments, dict):
            enrollments = {}
        normalized_providers = deepcopy(_DEFAULT_DATA["providers"])
        for provider_id, value in providers.items():
            if isinstance(value, dict):
                normalized_providers[provider_id] = value
        normalized_enrollments = deepcopy(_DEFAULT_DATA["enrollments"])
        for provider_id, value in enrollments.items():
            if isinstance(value, dict):
                normalized_enrollments[provider_id] = value
        return {
            **deepcopy(_DEFAULT_DATA),
            **payload,
            "providers": normalized_providers,
            "enrollments": normalized_enrollments,
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
