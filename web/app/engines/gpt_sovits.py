from __future__ import annotations

import copy
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from voice_core.pronunciation import strip_pronunciation_dashes

from ..settings import Settings
from .contracts import GenerationResult, ModelStatus, ReferenceAudio
from .runtime import release_cuda_memory


class GptSovitsAdapter:
    engine_id = "gpt_sovits_v2"

    def __init__(self, settings: Settings, config: dict[str, Any]) -> None:
        self._settings = settings
        self._config = config
        self._clone: Any | None = None
        self._tts: Any | None = None
        self._loaded_version: str | None = None
        self._load_lock = threading.RLock()

    @property
    def is_loaded(self) -> bool:
        return self._tts is not None

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        version = self._version(profile)
        loaded = self._tts is not None and self._loaded_version == version
        try:
            clone = self._load_clone()
            missing = clone.missing_models(
                clone.model_paths(self._settings.root, version)
            )
        except Exception as error:
            return ModelStatus(False, loaded, f"GPT-SoVITS 检查失败: {error}")
        paths = tuple(str(path) for path in missing)
        reason = "GPT-SoVITS 权重不完整" if paths else ""
        return ModelStatus(not paths, loaded, reason, paths)

    def generate(
        self,
        item: dict[str, Any],
        reference: ReferenceAudio,
        profile: dict[str, Any],
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int],
    ) -> GenerationResult:
        version = self._version(profile)
        clone = self._load_clone()
        config = copy.deepcopy(self._config["voice_clone"])
        config["version"] = version
        config.update(generation_settings)
        started = perf_counter()
        with self._working_directory():
            sample_rate, audio = clone.synthesize(
                self._tts_instance(config),
                strip_pronunciation_dashes(str(item["generated_text"])),
                reference.path,
                reference.prompt_text,
                reference.prompt_lang,
                config,
                seed,
            )
        clone.write_wav(output_path, audio, sample_rate)
        return GenerationResult(
            output_path,
            round(len(audio) / sample_rate, 3),
            f"gpt-sovits-{version}",
            perf_counter() - started,
        )

    def unload(self) -> None:
        with self._load_lock:
            self._tts = None
            self._loaded_version = None
        release_cuda_memory()

    def _load_clone(self) -> Any:
        with self._load_lock:
            if self._clone is None:
                code_root = str(self._settings.root / "code")
                if code_root not in sys.path:
                    sys.path.insert(0, code_root)
                import gpt_sovits_clone

                self._clone = gpt_sovits_clone
        return self._clone

    def _tts_instance(self, config: dict[str, Any]) -> Any:
        version = str(config["version"])
        with self._load_lock:
            if self._tts is not None and self._loaded_version == version:
                return self._tts
            if self._tts is not None:
                self._tts = None
                self._loaded_version = None
                release_cuda_memory()
            clone = self._load_clone()
            self._tts = clone.create_tts(self._settings.root, config)
            self._loaded_version = version
            return self._tts

    @staticmethod
    def _version(profile: dict[str, Any]) -> str:
        return str(profile.get("model_version") or "v2")

    @contextmanager
    def _working_directory(self) -> Iterator[None]:
        previous = Path.cwd()
        os.chdir(self._settings.root / "tools" / "GPT-SoVITS")
        try:
            yield
        finally:
            os.chdir(previous)
