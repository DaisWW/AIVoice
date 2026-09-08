from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import soundfile as sf

from voice_core.pronunciation import strip_pronunciation_dashes

from ..settings import Settings
from .contracts import GenerationResult, ModelStatus, ReferenceAudio
from .dependencies import missing_modules
from .runtime import release_cuda_memory, set_generation_seed


class VoxCpmAdapter:
    """Lazy local adapter for VoxCPM 1.5 and 2 voice cloning models."""

    engine_id = "voxcpm"
    _REQUIRED_MODULES = ("voxcpm",)
    _REQUIRED_FILES = (
        "config.json",
        "model.safetensors",
        "audiovae.pth",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
    )
    _DEFAULT_PARAMETERS = {
        "cfg_value": 2.0,
        "inference_timesteps": 10,
        "min_len": 2,
        "max_len": 512,
        "retry_badcase": True,
        "retry_badcase_max_times": 2,
        "retry_badcase_ratio_threshold": 6.0,
    }

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None
        self._loaded_path: Path | None = None
        self._architecture: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        model_path = self._model_path(profile)
        missing = [
            str(model_path / name)
            for name in self._REQUIRED_FILES
            if not (model_path / name).is_file()
        ]
        missing.extend(
            f"Python:{name}" for name in missing_modules(self._REQUIRED_MODULES)
        )
        reason = "VoxCPM 运行时或权重不完整" if missing else ""
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
        parameters = self._parameters(profile)
        text = strip_pronunciation_dashes(str(item["generated_text"]))
        reference_text = reference.prompt_text.strip()
        architecture = self._architecture or self._read_architecture(profile)

        kwargs: dict[str, Any] = {
            "cfg_value": parameters["cfg_value"],
            "inference_timesteps": parameters["inference_timesteps"],
            "min_len": parameters["min_len"],
            "max_len": parameters["max_len"],
            "retry_badcase": parameters["retry_badcase"],
            "retry_badcase_max_times": parameters["retry_badcase_max_times"],
            "retry_badcase_ratio_threshold": parameters[
                "retry_badcase_ratio_threshold"
            ],
        }
        if architecture == "voxcpm2":
            kwargs["reference_wav_path"] = str(reference.path)
            if reference_text:
                kwargs.update(
                    prompt_wav_path=str(reference.path),
                    prompt_text=reference_text,
                )
        elif architecture == "voxcpm":
            if not reference_text:
                raise RuntimeError("VoxCPM1.5 需要参考音频的准确逐字稿")
            kwargs.update(
                prompt_wav_path=str(reference.path),
                prompt_text=reference_text,
            )
        else:
            raise RuntimeError(f"VoxCPM 不支持的模型架构: {architecture}")

        del generation_settings
        wav = model.generate(text=text, **kwargs)
        audio = np.asarray(wav, dtype=np.float32).reshape(-1)
        if audio.size == 0 or not np.isfinite(audio).all():
            raise RuntimeError("VoxCPM 没有返回有效音频")
        sample_rate = int(model.tts_model.sample_rate)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, audio, sample_rate, subtype="PCM_16")
        return GenerationResult(
            output_path,
            round(audio.size / sample_rate, 3),
            f"voxcpm-{architecture}",
            perf_counter() - started,
        )

    def unload(self) -> None:
        self._model = None
        self._loaded_path = None
        self._architecture = None
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
        from voxcpm import VoxCPM

        self._model = VoxCPM.from_pretrained(
            str(model_path),
            load_denoiser=False,
            optimize=False,
            device="cuda:0" if torch.cuda.is_available() else "cpu",
        )
        self._loaded_path = model_path
        self._architecture = self._read_architecture(profile)
        return self._model

    def _model_path(self, profile: dict[str, Any]) -> Path:
        configured = str(profile.get("model_path") or "tools/models/voxcpm2")
        path = Path(configured)
        return path if path.is_absolute() else self._settings.root / path

    def _read_architecture(self, profile: dict[str, Any]) -> str:
        model_path = self._model_path(profile)
        try:
            payload = json.loads(
                (model_path / "config.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("VoxCPM 配置文件无法读取") from error
        return str(payload.get("architecture") or "").strip().lower()

    @classmethod
    def _parameters(cls, profile: dict[str, Any]) -> dict[str, float | int | bool]:
        configured = profile.get("voxcpm_parameters")
        if not isinstance(configured, dict):
            configured = {}
        parameters: dict[str, float | int | bool] = dict(cls._DEFAULT_PARAMETERS)
        for key in cls._DEFAULT_PARAMETERS:
            if key not in configured:
                continue
            value = configured[key]
            default = cls._DEFAULT_PARAMETERS[key]
            if isinstance(default, bool):
                if isinstance(value, bool):
                    parameters[key] = value
            elif isinstance(default, int):
                if isinstance(value, int) and not isinstance(value, bool):
                    parameters[key] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                parameters[key] = float(value)
        return parameters
