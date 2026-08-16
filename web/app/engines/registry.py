from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..profiles import Profiles
from ..provider_config import ProviderConfigStore
from ..settings import Settings
from .contracts import GenerationResult, ModelStatus, ReferenceAudio, VoiceAdapter
from .cosyvoice import CosyVoice3Adapter
from .elevenlabs import ElevenLabsAdapter
from .gpt_sovits import GptSovitsAdapter
from .minimax import MiniMaxAdapter
from .qwen_tts import Qwen3TtsAdapter
from .reference_audio import ReferenceAudioBuilder


class VoiceEngine:
    """Route one GPU queue across lazy, mutually exclusive clone engines."""

    def __init__(
        self,
        settings: Settings,
        profiles: Profiles,
        provider_config: ProviderConfigStore,
    ) -> None:
        self._profiles = profiles
        config = json.loads(settings.clone_config_path.read_text(encoding="utf-8"))
        adapters: list[VoiceAdapter] = [
            GptSovitsAdapter(settings, config),
            CosyVoice3Adapter(settings),
            Qwen3TtsAdapter(settings),
            ElevenLabsAdapter(provider_config),
            MiniMaxAdapter(provider_config),
        ]
        self._adapters = {adapter.engine_id: adapter for adapter in adapters}
        self._references = ReferenceAudioBuilder(settings.root, config)
        self._active: VoiceAdapter | None = None
        self._lock = threading.RLock()

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        adapter = self._adapters.get(provider_id)
        tester = getattr(adapter, "test_connection", None)
        if not callable(tester):
            raise ValueError(f"未知或不可检测的 provider: {provider_id}")
        return tester()

    @property
    def is_loaded(self) -> bool:
        return any(adapter.is_loaded for adapter in self._adapters.values())

    def model_status(self) -> dict[str, Any]:
        statuses: dict[str, dict[str, Any]] = {}
        required_missing: set[str] = set()
        for profile in self._profiles.all():
            status = self._status(profile).public()
            statuses[str(profile["id"])] = status
            if profile.get("required"):
                required_missing.update(
                    path
                    for path in status.get("missing_files", [])
                    if not str(path).startswith("Python:")
                )
        return {
            "loaded": self.is_loaded,
            "missing_models": sorted(required_missing),
            "models": statuses,
        }

    def model_available(self, model_id: str) -> ModelStatus:
        return self._status(self._profiles.model(model_id))

    def prepare_reference(
        self, voice_files: list[dict[str, Any]], target_path: Path
    ) -> ReferenceAudio:
        return self._references.build(voice_files, target_path)

    def generate(
        self,
        item: dict[str, Any],
        reference: ReferenceAudio,
        model_id: str,
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int] | None = None,
    ) -> GenerationResult:
        profile = self._profiles.model(model_id)
        adapter = self._adapter(profile)
        settings = self._profiles.resolve_generation_settings(
            model_id, generation_settings
        )
        with self._lock:
            self._activate(adapter)
            return adapter.generate(
                item,
                reference,
                profile,
                seed,
                output_path,
                settings,
            )

    def unload(self) -> None:
        with self._lock:
            if self._active is not None:
                self._active.unload()
                self._active = None

    def _status(self, profile: dict[str, Any]) -> ModelStatus:
        adapter = self._adapters.get(str(profile["engine"]))
        if adapter is None:
            reason = str(profile.get("availability_reason") or "当前工作流尚未接入该模型")
            return ModelStatus(False, False, reason)
        if profile.get("available") is False:
            reason = str(profile.get("availability_reason") or "该模型当前已禁用")
            return ModelStatus(False, adapter.is_loaded, reason)
        return adapter.status(profile)

    def _adapter(self, profile: dict[str, Any]) -> VoiceAdapter:
        status = self._status(profile)
        if not status.available:
            detail = ", ".join(status.missing_files)
            raise RuntimeError(f"{status.reason}{': ' + detail if detail else ''}")
        return self._adapters[str(profile["engine"])]

    def _activate(self, adapter: VoiceAdapter) -> None:
        if self._active is adapter:
            return
        if self._active is not None:
            self._active.unload()
        self._active = adapter
