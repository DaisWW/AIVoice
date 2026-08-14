from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..services import ApplicationServices
from ..storage import ensure_within, safe_filename


class JobDownloadService:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services

    def item_path(
        self, job: dict[str, Any], item_id: str
    ) -> tuple[dict[str, Any], Path]:
        item = self._services.database.jobs.item(str(job["id"]), item_id)
        if not item:
            raise HTTPException(status_code=404, detail="该段音频尚未生成")
        return item, self._clone_path(item, "该段音频尚未生成")

    def candidate_path(
        self,
        job: dict[str, Any],
        item_id: str,
        candidate_id: str,
    ) -> tuple[dict[str, Any], Path]:
        item = self._services.database.jobs.item(str(job["id"]), item_id)
        candidate = self._services.database.candidates.get_for_job(
            str(job["id"]), candidate_id
        )
        if (
            not item
            or not candidate
            or candidate["kind"] != "gpt"
            or str(candidate["job_item_id"]) != str(item_id)
        ):
            raise HTTPException(status_code=404, detail="找不到该段克隆音频")
        return candidate, self._clone_path(candidate, "该候选音频尚未生成")

    def archive_response(self, job: dict[str, Any], display_name: str) -> FileResponse:
        job_id = str(job["id"])
        items = self._downloadable_items(job)
        archive_path = self._services.settings.export_root / f"{job_id}.zip"
        with self._services.export_lock:
            self._build_archive(archive_path, items)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"{safe_filename(display_name)}_{job_id}.zip",
        )

    def accepted_archive_response(
        self, job: dict[str, Any], display_name: str
    ) -> FileResponse:
        items = self._services.database.candidates.accepted_items(str(job["id"]))
        if len(items) != int(job["total_items"]):
            raise HTTPException(
                status_code=409,
                detail="请先为每一段选择最终采用的克隆候选",
            )
        archive_path = self._services.settings.export_root / f"{job['id']}-accepted.zip"
        with self._services.export_lock:
            self._build_accepted_archive(archive_path, job, items)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"{safe_filename(display_name)}_正式采用_{job['id']}.zip",
        )

    def _downloadable_items(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        if job["status"] in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务结束后才能全部下载")
        items = [
            item
            for item in self._services.database.jobs.items(str(job["id"]))
            if item.get("raw_audio_path") or item.get("audio_path")
        ]
        if not items:
            raise HTTPException(status_code=409, detail="任务尚无可下载音频")
        return items

    def _clone_path(self, item: dict[str, Any], missing_message: str) -> Path:
        value = str(item.get("raw_audio_path") or item.get("audio_path") or "")
        if not value:
            raise HTTPException(status_code=404, detail=missing_message)
        path = ensure_within(Path(value), self._services.settings.root)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="服务器上的音频文件已不存在")
        return path

    def _build_archive(self, archive_path: Path, items: list[dict[str, Any]]) -> None:
        temporary_path = archive_path.with_suffix(".zip.tmp")
        try:
            manifest = self._write_archive(temporary_path, items)
            if not manifest:
                raise HTTPException(status_code=409, detail="任务音频文件已不存在")
            temporary_path.replace(archive_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _write_archive(
        self, target: Path, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        manifest: list[dict[str, Any]] = []
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in items:
                self._add_item(archive, manifest, item)
            if manifest:
                archive.writestr(
                    "台本与发音.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
        return manifest

    def _build_accepted_archive(
        self,
        archive_path: Path,
        job: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> None:
        temporary_path = archive_path.with_suffix(".zip.tmp")
        try:
            manifest: list[dict[str, Any]] = []
            with zipfile.ZipFile(
                temporary_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for item in items:
                    path = self._clone_path(item, f"第 {item['sequence']} 段采用的音频已不存在")
                    archive_name = f"Audio/{int(item['sequence']):03d}.wav"
                    archive.write(path, archive_name)
                    manifest.append(
                        {
                            "id": f"{job['id']}_{int(item['sequence']):03d}",
                            "sequence": int(item["sequence"]),
                            "text": item["text"],
                            "pronunciation": item["pronunciation"],
                            "direction": item["direction"],
                            "candidateId": item["id"],
                            "audio": archive_name,
                            "durationSeconds": item["duration_seconds"],
                        }
                    )
                archive.writestr(
                    "UnityAudioManifest.json",
                    json.dumps(
                        {"version": 1, "jobId": job["id"], "items": manifest},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            temporary_path.replace(archive_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _add_item(
        self,
        archive: zipfile.ZipFile,
        manifest: list[dict[str, Any]],
        item: dict[str, Any],
    ) -> None:
        try:
            path = self._clone_path(item, "")
        except HTTPException:
            return
        archive_name = f"{int(item['sequence']):03d}.wav"
        archive.write(path, archive_name)
        manifest.append(
            {
                "sequence": item["sequence"],
                "text": item["text"],
                "pronunciation": item["pronunciation"],
                "file": archive_name,
            }
        )
