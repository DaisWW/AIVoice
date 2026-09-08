from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from scipy.io import wavfile

from voice_core.pronunciation import strip_pronunciation_dashes

from ..settings import Settings
from .contracts import GenerationResult, ModelStatus, ReferenceAudio
from .dependencies import add_import_paths, missing_modules
from .runtime import release_cuda_memory, set_generation_seed


class CosyVoice3Adapter:
    engine_id = "cosyvoice3"
    _INSTRUCT_PREFIX = "You are a helpful assistant.<|endofprompt|>"
    _REQUIRED_MODULES = (
        "conformer",
        "diffusers",
        "gdown",
        "hydra",
        "hyperpyyaml",
        "inflect",
        "lightning",
        "modelscope",
        "omegaconf",
        "onnxruntime",
        "pyworld",
        "whisper",
    )
    _REQUIRED_FILES = (
        "cosyvoice3.yaml",
        "llm.pt",
        "flow.pt",
        "hift.pt",
        "campplus.onnx",
        "speech_tokenizer_v3.onnx",
        "CosyVoice-BlankEN/config.json",
        "CosyVoice-BlankEN/model.safetensors",
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None
        self._loaded_path: Path | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        self._configure_imports()
        source = self._source_root / "cosyvoice" / "cli" / "cosyvoice.py"
        model_path = self._model_path(profile)
        missing = [str(source)] if not source.is_file() else []
        missing.extend(
            str(model_path / name)
            for name in self._REQUIRED_FILES
            if not (model_path / name).is_file()
        )
        modules = missing_modules(self._REQUIRED_MODULES)
        if modules:
            missing.extend(f"Python:{name}" for name in modules)
        reason = "CosyVoice3 运行时或权重不完整" if missing else ""
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
        started = perf_counter()
        target_text = self._INSTRUCT_PREFIX + strip_pronunciation_dashes(
            str(item["generated_text"])
        )
        chunks = model.inference_cross_lingual(
            target_text,
            str(reference.path),
            stream=False,
            speed=float(generation_settings.get("speed_factor", 1.0)),
            text_frontend=False,
        )
        audio = self._collect_audio(chunks)
        self._write(output_path, audio, int(model.sample_rate))
        return GenerationResult(
            output_path,
            round(audio.size / int(model.sample_rate), 3),
            "cosyvoice3",
            perf_counter() - started,
        )

    def unload(self) -> None:
        self._model = None
        self._loaded_path = None
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
        from cosyvoice.cli.cosyvoice import AutoModel

        self._model = AutoModel(
            model_dir=str(model_path),
            fp16=True,
            load_trt=False,
            load_vllm=False,
        )
        self._loaded_path = model_path
        return self._model

    def _configure_imports(self) -> None:
        add_import_paths(
            self._source_root,
            self._source_root / "third_party" / "Matcha-TTS",
        )

    @property
    def _source_root(self) -> Path:
        return self._settings.root / "tools" / "CosyVoice"

    def _model_path(self, profile: dict[str, Any]) -> Path:
        configured = str(profile.get("model_path") or "tools/models/cosyvoice3")
        path = Path(configured)
        return path if path.is_absolute() else self._settings.root / path

    @staticmethod
    def _collect_audio(chunks: Any) -> np.ndarray:
        parts = [
            output["tts_speech"].detach().float().cpu().numpy().reshape(-1)
            for output in chunks
        ]
        if not parts:
            raise RuntimeError("CosyVoice3 没有生成音频")
        return np.concatenate(parts).astype(np.float32)

    @staticmethod
    def _write(path: Path, audio: np.ndarray, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        samples = np.int16(np.clip(audio, -1.0, 1.0) * 32767)
        wavfile.write(path, sample_rate, samples)
