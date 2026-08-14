from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

from gpt_sovits_audio import (
    AUDIO_EXTENSIONS,
    build_reference_audio,
    decode_audio,
    resample_audio,
    to_float_audio,
    write_wav,
)


__all__ = [
    "AUDIO_EXTENSIONS",
    "build_reference_audio",
    "create_tts",
    "decode_audio",
    "missing_models",
    "model_paths",
    "resample_audio",
    "synthesize",
    "to_float_audio",
    "write_wav",
]


_MODEL_WEIGHTS = {
    "v2": (
        Path("gsv-v2final-pretrained")
        / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
        Path("gsv-v2final-pretrained") / "s2G2333k.pth",
    ),
    "v2ProPlus": (
        Path("s1v3.ckpt"),
        Path("v2Pro") / "s2Gv2ProPlus.pth",
    ),
}


def model_paths(root: Path, version: str = "v2") -> dict[str, Path]:
    try:
        t2s_relative, vits_relative = _MODEL_WEIGHTS[version]
    except KeyError as error:
        raise ValueError(f"不支持的 GPT-SoVITS 版本: {version}") from error
    gpt_root = root / "tools" / "GPT-SoVITS"
    pretrained = gpt_root / "GPT_SoVITS" / "pretrained_models"
    return {
        "gpt_root": gpt_root,
        "bert": pretrained / "chinese-roberta-wwm-ext-large",
        "hubert": pretrained / "chinese-hubert-base",
        "t2s": pretrained / t2s_relative,
        "vits": pretrained / vits_relative,
        "langdetect": pretrained / "fast_langdetect" / "lid.176.bin",
        "g2pw": gpt_root / "GPT_SoVITS" / "text" / "G2PWModel",
    }


def missing_models(paths: dict[str, Path]) -> list[Path]:
    required = [
        paths["bert"] / "config.json",
        paths["bert"] / "tokenizer.json",
        paths["bert"] / "model.safetensors",
        paths["hubert"] / "config.json",
        paths["hubert"] / "preprocessor_config.json",
        paths["hubert"] / "model.safetensors",
        paths["t2s"],
        paths["vits"],
        paths["langdetect"],
    ]
    missing = [path for path in required if not path.is_file()]
    if not paths["g2pw"].is_dir() or not any(paths["g2pw"].rglob("*.onnx")):
        missing.append(paths["g2pw"])
    return missing


def create_tts(root: Path, settings: dict[str, Any]) -> Any:
    version = str(settings.get("version") or "v2")
    paths = model_paths(root, version)
    missing = missing_models(paths)
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(f"GPT-SoVITS 模型不完整:\n{formatted}")
    for path in (paths["gpt_root"], paths["gpt_root"] / "GPT_SoVITS"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    os.environ["bert_path"] = str(paths["bert"])
    os.environ["cnhubert_base_path"] = str(paths["hubert"])
    os.environ["HF_HOME"] = str(root / "web" / "data" / "cache" / "huggingface")

    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

    config = TTS_Config(
        {
            "custom": {
                "device": settings.get("device", "cuda"),
                "is_half": bool(settings.get("is_half", True)),
                "version": version,
                "t2s_weights_path": str(paths["t2s"]),
                "vits_weights_path": str(paths["vits"]),
                "bert_base_path": str(paths["bert"]),
                "cnhuhbert_base_path": str(paths["hubert"]),
            }
        }
    )
    return TTS(config)


def synthesize(
    tts: Any,
    text: str,
    reference_path: Path,
    reference_prompt_text: str,
    reference_prompt_lang: str,
    settings: dict[str, Any],
    seed: int,
) -> tuple[int, np.ndarray]:
    inputs = {
        "text": text,
        "text_lang": settings.get("text_lang", "all_zh"),
        "ref_audio_path": str(reference_path),
        "aux_ref_audio_paths": [],
        "prompt_text": reference_prompt_text,
        "prompt_lang": reference_prompt_lang or settings.get("prompt_lang", "all_zh"),
        "top_k": int(settings.get("top_k", 15)),
        "top_p": float(settings.get("top_p", 0.9)),
        "temperature": float(settings.get("temperature", 0.8)),
        "text_split_method": settings.get("text_split_method", "cut0"),
        "batch_size": 1,
        "split_bucket": False,
        "speed_factor": float(settings.get("speed_factor", 1.0)),
        "fragment_interval": float(settings.get("fragment_interval", 0.18)),
        "seed": seed,
        "parallel_infer": True,
        "repetition_penalty": float(settings.get("repetition_penalty", 1.25)),
        "return_fragment": False,
        "streaming_mode": False,
    }
    chunks: list[np.ndarray] = []
    sample_rate: int | None = None
    for output_rate, output_audio in tts.run(inputs):
        clip = to_float_audio(np.asarray(output_audio))
        if sample_rate is None:
            sample_rate = int(output_rate)
        elif int(output_rate) != sample_rate:
            clip = resample_audio(clip, int(output_rate), sample_rate)
        chunks.append(clip)
    if sample_rate is None or not chunks:
        raise RuntimeError("GPT-SoVITS 没有返回音频")
    audio = np.concatenate(chunks)
    if sample_rate <= 0 or audio.size == 0:
        raise RuntimeError("GPT-SoVITS 返回了空音频")
    return sample_rate, audio
