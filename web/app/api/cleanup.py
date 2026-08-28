from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..services import ApplicationServices
from ..storage import ensure_within


def _safe_id(value: str) -> str | None:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return None
    path = Path(value)
    if (
        path.is_absolute()
        or path.name != value
        or any(part in {".", ".."} for part in path.parts)
    ):
        return None
    return value


def remove_job_artifacts(services: ApplicationServices, job_id: str) -> None:
    """Remove only the exact output paths owned by one deleted job."""
    with services.export_lock:
        job_id = _safe_id(job_id)
        if job_id is None:
            return
        root = services.settings.root
        try:
            directory = ensure_within(services.settings.job_root / job_id, root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        if directory.is_dir():
            try:
                shutil.rmtree(directory)
            except OSError:
                pass
        remove_job_exports(services, job_id)


def remove_job_exports(services: ApplicationServices, job_id: str) -> None:
    with services.export_lock:
        job_id = _safe_id(job_id)
        if job_id is None:
            return
        root = services.settings.root
        for suffix in (".zip", ".zip.tmp", "-accepted.zip", "-accepted.zip.tmp"):
            try:
                archive = ensure_within(
                    services.settings.export_root / f"{job_id}{suffix}", root
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                continue


def remove_script_exports(services: ApplicationServices, script_id: str) -> None:
    with services.export_lock:
        script_id = _safe_id(script_id)
        if script_id is None:
            return
        root = services.settings.root
        for suffix in (
            "-accepted.zip",
            "-accepted.zip.tmp",
            "-all.zip",
            "-all.zip.tmp",
        ):
            try:
                archive = ensure_within(
                    services.settings.export_root / f"{script_id}{suffix}", root
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                continue


def remove_item_artifacts(
    services: ApplicationServices,
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    """Remove only files belonging to one job item and its candidates."""
    with services.export_lock:
        try:
            job_directory = ensure_within(
                services.settings.job_root / str(item["job_id"]),
                services.settings.root,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        paths: list[Path] = []
        for record in [item, *candidates]:
            for field in ("raw_audio_path", "audio_path"):
                value = str(record.get(field) or "")
                if not value:
                    continue
                try:
                    path = ensure_within(Path(value), job_directory)
                except (OSError, RuntimeError, TypeError, ValueError):
                    continue
                paths.append(path)
                if path.is_file() or path.is_symlink():
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue
        for path in paths:
            _remove_empty_parents(path.parent, job_directory)


def _remove_empty_parents(directory: Path, stop: Path) -> None:
    current = directory
    while current != stop and current.is_dir():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
