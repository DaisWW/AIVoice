from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import soundfile as sf

from voice_core.pronunciation import strip_pronunciation_dashes

from ..settings import Settings
from .contracts import GenerationResult, ModelStatus, ReferenceAudio
from .dependencies import add_import_paths, missing_modules
from .runtime import release_cuda_memory, set_generation_seed


class Qwen3TtsAdapter:
    engine_id = "qwen3_tts"
    _REQUIRED_MODULES = ("accelerate", "einops", "qwen_tts", "sox", "transformers")
    _REQUIRED_FILES = (
        "config.json",
        "model.safetensors",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "speech_tokenizer/config.json",
        "speech_tokenizer/configuration.json",
        "speech_tokenizer/model.safetensors",
        "speech_tokenizer/preprocessor_config.json",
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None
        self._loaded_path: Path | None = None
        self._prompt_key: tuple[str, int, str] | None = None
        self._prompt: Any | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        add_import_paths(self._source_root)
        model_path = self._model_path(profile)
        missing = [
            str(model_path / name)
            for name in self._REQUIRED_FILES
            if not (model_path / name).is_file()
        ]
        modules = missing_modules(self._REQUIRED_MODULES)
        missing.extend(f"Python:{name}" for name in modules)
        if "transformers" not in modules and not self._transformers_supported():
            missing.append("Python:transformers>=4.57.3")
        reason = "Qwen3-TTS 运行时或权重不完整" if missing else ""
        loaded = self._model is not None and self._loaded_path == model_path
        return ModelStatus(not missing, loaded, reason, tuple(missing))

    def generate(
        self,
        item: dict[str, Any],
        reference: ReferenceAudio,
        profile: dict[str, Any],
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int],
    ) -> GenerationResult:
        model = self._load(profile)
        set_generation_seed(seed)
        prompt = self._voice_prompt(model, reference.path, reference.prompt_text)
        started = perf_counter()
        wavs, sample_rate = model.generate_voice_clone(
            text=strip_pronunciation_dashes(str(item["generated_text"])),
            language="Auto",
            voice_clone_prompt=prompt,
            non_streaming_mode=True,
            do_sample=True,
            **generation_settings,
        )
        audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, audio, int(sample_rate), subtype="PCM_16")
        return GenerationResult(
            output_path,
            round(audio.size / int(sample_rate), 3),
            "qwen3-tts",
            perf_counter() - started,
        )

    def unload(self) -> None:
        self._model = None
        self._loaded_path = None
        self._prompt_key = None
        self._prompt = None
        release_cuda_memory()

    def _load(self, profile: dict[str, Any]) -> Any:
        model_path = self._model_path(profile)
        if self._model is not None:
            if self._loaded_path == model_path:
                return self._model
            self.unload()
        status = self.status(profile)
        if not status.available:
            raise RuntimeError(status.reason + ": " + ", ".join(status.missing_files))
        import torch
        from qwen_tts import Qwen3TTSModel

        use_cuda = torch.cuda.is_available()
        self._model = Qwen3TTSModel.from_pretrained(
            str(model_path),
            device_map="cuda:0" if use_cuda else "cpu",
            dtype=torch.bfloat16 if use_cuda else torch.float32,
            attn_implementation="sdpa",
        )
        self._loaded_path = model_path
        return self._model

    def _voice_prompt(self, model: Any, path: Path, reference_text: str) -> Any:
        normalized_text = reference_text.strip()
        key = (str(path), path.stat().st_mtime_ns, normalized_text)
        if self._prompt_key != key:
            options: dict[str, Any] = {
                "ref_audio": str(path),
                "x_vector_only_mode": not normalized_text,
            }
            if normalized_text:
                options["ref_text"] = normalized_text
            self._prompt = model.create_voice_clone_prompt(**options)
            self._prompt_key = key
        return self._prompt

    @property
    def _source_root(self) -> Path:
        return self._settings.root / "tools" / "Qwen3-TTS"

    def _model_path(self, profile: dict[str, Any]) -> Path:
        configured = str(profile.get("model_path") or "tools/models/qwen3_tts_0_6b")
        path = Path(configured)
        return path if path.is_absolute() else self._settings.root / path

    @staticmethod
    def _transformers_supported() -> bool:
        try:
            from packaging.version import InvalidVersion, Version

            installed = Version(version("transformers"))
        except (ImportError, InvalidVersion, PackageNotFoundError):
            return False
        return installed >= Version("4.57.3")
