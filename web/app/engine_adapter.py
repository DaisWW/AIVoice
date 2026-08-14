from __future__ import annotations

import copy
import json
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from voice_core.pronunciation import strip_pronunciation_dashes

from .generation_settings import normalize_generation_settings
from .profiles import Profiles
from .settings import Settings


@dataclass(frozen=True)
class ReferenceAudio:
    path: Path
    prompt_text: str
    prompt_lang: str
    duration_seconds: float


@dataclass(frozen=True)
class GenerationResult:
    audio_path: Path
    duration_seconds: float
    processing_backend: str
    elapsed_seconds: float


class VoiceEngine:
    """Lazy GPT-SoVITS V2 voice-cloning adapter."""

    def __init__(self, settings: Settings, profiles: Profiles) -> None:
        self._settings = settings
        self._profiles = profiles
        self._config = json.loads(
            settings.clone_config_path.read_text(encoding="utf-8")
        )
        self._clone: Any | None = None
        self._tts: Any | None = None
        self._missing_models: tuple[str, ...] | None = None
        self._load_lock = threading.RLock()

    @property
    def is_loaded(self) -> bool:
        return self._tts is not None

    def model_status(self) -> dict[str, Any]:
        with self._load_lock:
            if self._missing_models is None:
                clone = self._load_clone()
                missing = clone.missing_models(clone.model_paths(self._settings.root))
                self._missing_models = tuple(str(path) for path in missing)
        return {
            "loaded": self.is_loaded,
            "missing_models": list(self._missing_models),
        }

    def _load_clone(self) -> Any:
        with self._load_lock:
            if self._clone is None:
                code_root = str(self._settings.root / "code")
                if code_root not in sys.path:
                    sys.path.insert(0, code_root)
                import gpt_sovits_clone

                self._clone = gpt_sovits_clone
        return self._clone

    @contextmanager
    def _gpt_working_directory(self) -> Iterator[None]:
        previous = Path.cwd()
        os.chdir(self._settings.root / "tools" / "GPT-SoVITS")
        try:
            yield
        finally:
            os.chdir(previous)

    def _tts_instance(self) -> Any:
        if self._tts is None:
            clone = self._load_clone()
            with self._gpt_working_directory():
                self._tts = clone.create_tts(
                    self._settings.root, self._config["voice_clone"]
                )
        return self._tts

    def prepare_reference(
        self, voice_files: list[dict[str, Any]], target_path: Path
    ) -> ReferenceAudio:
        if not voice_files:
            raise ValueError("所选声音库没有启用的录音")
        clone = self._load_clone()
        records = [
            {
                "voice_id": item["voice_id"],
                "order": index + 1,
                "enabled": True,
                "audio_path": Path(item["source_path"]),
                "reference_text": "",
                "reference_lang": "all_zh",
                "notes": "",
            }
            for index, item in enumerate(voice_files)
        ]
        reference = self._config["voice_clone"]["reference"]
        path, _, duration, prompt_text, prompt_lang = clone.build_reference_audio(
            records,
            target_path,
            int(self._config["reference_sample_rate"]),
            float(reference.get("target_seconds", 6.0)),
            float(reference.get("max_seconds", 9.5)),
            float(reference.get("gap_ms", 120)) / 1000.0,
            self._config["trim"],
        )
        return ReferenceAudio(
            path=path,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            duration_seconds=duration,
        )

    def generate(
        self,
        item: dict[str, Any],
        reference: ReferenceAudio,
        model_id: str,
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int] | None = None,
    ) -> GenerationResult:
        clone = self._load_clone()
        settings = self._generation_config(model_id, generation_settings)
        started = perf_counter()
        sample_rate, audio = self._synthesize(clone, item, reference, settings, seed)
        clone.write_wav(output_path, audio, sample_rate)
        return GenerationResult(
            audio_path=output_path,
            duration_seconds=round(len(audio) / sample_rate, 3),
            processing_backend="gpt-sovits-v2",
            elapsed_seconds=perf_counter() - started,
        )

    def _generation_config(
        self,
        model_id: str,
        generation_settings: dict[str, float | int] | None,
    ) -> dict[str, Any]:
        settings = copy.deepcopy(self._config["voice_clone"])
        settings.update(self._profiles.model(model_id).get("clone_overrides", {}))
        settings.update(normalize_generation_settings(generation_settings or {}))
        return settings

    def _synthesize(
        self,
        clone: Any,
        item: dict[str, Any],
        reference: ReferenceAudio,
        settings: dict[str, Any],
        seed: int,
    ) -> tuple[int, Any]:
        with self._gpt_working_directory():
            return clone.synthesize(
                self._tts_instance(),
                strip_pronunciation_dashes(str(item["generated_text"])),
                reference.path,
                reference.prompt_text,
                reference.prompt_lang,
                settings,
                seed,
            )
