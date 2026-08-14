from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import UploadFile


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def safe_filename(value: str, fallback: str = "upload") -> str:
    name = Path(value or fallback).name
    name = SAFE_NAME_RE.sub("_", name).strip(" .")
    return name or fallback


async def save_upload(upload: UploadFile, directory: Path) -> tuple[Path, str, int]:
    original_name = safe_filename(upload.filename or "upload")
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex[:12]}_{original_name}"
    target = directory / stored_name
    total = 0
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("单个上传文件不能超过 200 MB")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target, original_name, total


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"路径超出工作区: {resolved}")
    return resolved


def validate_wav(path: Path) -> None:
    """Fail early on an empty or corrupt template before it reaches the GPU queue."""
    try:
        from scipy.io import wavfile

        sample_rate, audio = wavfile.read(path)
    except Exception as error:
        raise ValueError(f"WAV 无法读取: {path.name}") from error
    if int(sample_rate) < 8_000 or getattr(audio, "size", 0) == 0:
        raise ValueError(f"WAV 采样率或内容无效: {path.name}")
