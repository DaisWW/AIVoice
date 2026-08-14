from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


MODEL_SPECS = (
    (
        "chinese-roberta-wwm-ext-large",
        "e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8",
        True,
        ("bert.embeddings.position_ids",),
    ),
    (
        "chinese-hubert-base",
        "24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c",
        False,
        (),
    ),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_safetensors(path: Path) -> bool:
    if not path.is_file():
        return False
    from safetensors import SafetensorError, safe_open

    try:
        with safe_open(str(path), framework="pt", device="cpu") as weights:
            return bool(weights.keys())
    except (OSError, SafetensorError):
        return False


def convert(project_root: Path) -> list[Path]:
    pretrained_root = (
        project_root / "tools" / "GPT-SoVITS" / "GPT_SoVITS" / "pretrained_models"
    )
    return [
        _convert_model(pretrained_root / directory, expected, masked_lm, ignored)
        for directory, expected, masked_lm, ignored in MODEL_SPECS
    ]


def _convert_model(
    model_root: Path,
    expected_hash: str,
    masked_lm: bool,
    ignored_keys: tuple[str, ...],
) -> Path:
    source = model_root / "pytorch_model.bin"
    target = model_root / "model.safetensors"
    if _valid_safetensors(target):
        return target
    if not source.is_file():
        raise FileNotFoundError(f"找不到待转换权重: {source}")
    actual_hash = file_sha256(source)
    if actual_hash != expected_hash:
        raise ValueError(f"权重 SHA-256 不匹配: {source} ({actual_hash})")

    import torch
    from safetensors.torch import save_model
    from transformers import AutoConfig, AutoModel, AutoModelForMaskedLM

    config = AutoConfig.from_pretrained(model_root, local_files_only=True)
    factory = AutoModelForMaskedLM if masked_lm else AutoModel
    model = factory.from_config(config)
    state = torch.load(source, map_location="cpu", weights_only=True)
    for key in ignored_keys:
        state.pop(key, None)
    model.load_state_dict(state, strict=True)
    temporary = target.with_suffix(".safetensors.tmp")
    temporary.unlink(missing_ok=True)
    try:
        save_model(model, str(temporary), metadata={"format": "pt"})
        if not _valid_safetensors(temporary):
            raise RuntimeError(f"safetensors 转换失败: {model_root}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="转换 GPT-SoVITS 安全权重")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    for target in convert(parser.parse_args().root.resolve()):
        print(f"safetensors 已就绪: {target}")


if __name__ == "__main__":
    main()
