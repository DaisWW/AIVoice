from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_SNAPSHOT_PREFIX = ".download-"
DOWNLOAD_SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60


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
            while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("单个上传文件不能超过 200 MB")
                await run_in_threadpool(handle.write, chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target, original_name, total


async def read_upload(upload: UploadFile) -> bytes:
    """Read an upload in bounded chunks while enforcing the upload size limit."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("单个上传文件不能超过 200 MB")
        chunks.append(chunk)
    return b"".join(chunks)


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"路径超出工作区: {resolved}")
    return resolved


def resolve_audio_path(record: Mapping[str, object], root: Path) -> Path | None:
    """Return the first safe, existing audio path, preferring the raw output."""
    for field in ("raw_audio_path", "audio_path"):
        try:
            value = record.get(field)  # type: ignore[attr-defined]
        except AttributeError:
            try:
                value = record[field]  # type: ignore[index]
            except (KeyError, IndexError, TypeError):
                value = ""
        value = str(value or "").strip()
        if not value:
            continue
        try:
            path = ensure_within(Path(value), root)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if path.is_file():
            return path
    return None


def cleanup_download_snapshots(
    export_root: Path,
    *,
    max_age_seconds: float = DOWNLOAD_SNAPSHOT_MAX_AGE_SECONDS,
) -> None:
    """Remove stale download snapshots left by interrupted responses."""
    cutoff = time.time() - max(0.0, max_age_seconds)
    try:
        entries = export_root.iterdir()
    except OSError:
        return
    for path in entries:
        if not path.name.startswith(DOWNLOAD_SNAPSHOT_PREFIX):
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink(missing_ok=True)
        except OSError:
            continue


def validate_wav(path: Path) -> None:
    """Fail early on an empty or corrupt template before it reaches the GPU queue."""
    try:
        from scipy.io import wavfile

        sample_rate, audio = wavfile.read(path)
    except Exception as error:
        raise ValueError(f"WAV 无法读取: {path.name}") from error
    if int(sample_rate) < 8_000 or getattr(audio, "size", 0) == 0:
        raise ValueError(f"WAV 采样率或内容无效: {path.name}")
