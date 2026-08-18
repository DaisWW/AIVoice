from __future__ import annotations

import shutil

from ..services import ApplicationServices
from ..storage import ensure_within


def remove_job_artifacts(services: ApplicationServices, job_id: str) -> None:
    """Remove only the exact output paths owned by one deleted job."""
    root = services.settings.root
    directory = ensure_within(services.settings.job_root / job_id, root)
    if directory.is_dir():
        shutil.rmtree(directory)
    for suffix in (".zip", "-accepted.zip"):
        archive = ensure_within(services.settings.export_root / f"{job_id}{suffix}", root)
        archive.unlink(missing_ok=True)
