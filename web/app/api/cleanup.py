from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..services import ApplicationServices
from ..storage import ensure_within


def remove_job_artifacts(services: ApplicationServices, job_id: str) -> None:
    """Remove only the exact output paths owned by one deleted job."""
    root = services.settings.root
    directory = ensure_within(services.settings.job_root / job_id, root)
    if directory.is_dir():
        shutil.rmtree(directory)
    remove_job_exports(services, job_id)


def remove_job_exports(services: ApplicationServices, job_id: str) -> None:
    root = services.settings.root
    for suffix in (".zip", "-accepted.zip"):
        archive = ensure_within(
            services.settings.export_root / f"{job_id}{suffix}", root
        )
        archive.unlink(missing_ok=True)


def remove_item_artifacts(
    services: ApplicationServices,
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    """Remove only files belonging to one job item and its candidates."""
    job_directory = ensure_within(
        services.settings.job_root / str(item["job_id"]), services.settings.root
    )
    paths: list[Path] = []
    for record in [item, *candidates]:
        for field in ("raw_audio_path", "audio_path"):
            value = str(record.get(field) or "")
            if not value:
                continue
            path = ensure_within(Path(value), job_directory)
            paths.append(path)
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
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
