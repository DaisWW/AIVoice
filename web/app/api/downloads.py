from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ..services import ApplicationServices
from ..storage import (
    DOWNLOAD_SNAPSHOT_PREFIX,
    ensure_within,
    resolve_audio_path,
    safe_filename,
)


_ARCHIVE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CORRUPT_RECORD = "记录损坏，无法导出"


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

    def item_response(
        self,
        job: dict[str, Any],
        item_id: str,
        *,
        filename: str | None = None,
    ) -> FileResponse:
        with self._services.job_mutation_lock:
            _, path = self.item_path(job, item_id)
            return self.audio_response(path, filename)

    def candidate_response(
        self,
        job: dict[str, Any],
        item_id: str,
        candidate_id: str,
        *,
        filename: str | None = None,
    ) -> FileResponse:
        with self._services.job_mutation_lock:
            _, path = self.candidate_path(job, item_id, candidate_id)
            return self.audio_response(path, filename)

    def archive_response(self, job: dict[str, Any], display_name: str) -> FileResponse:
        job_id = str(job["id"])
        with self._services.job_mutation_lock:
            current = self._current_job(job_id)
            items = self._downloadable_items(current)
            archive_path = self._archive_path(job_id, ".zip")
            with self._services.export_lock:
                self._build_archive(archive_path, items)
                response = self._snapshot_response(
                    archive_path,
                    f"{safe_filename(display_name)}_{job_id}.zip",
                )
        return response

    def accepted_archive_response(
        self, job: dict[str, Any], display_name: str
    ) -> FileResponse:
        job_id = str(job["id"])
        with self._services.job_mutation_lock:
            current = self._current_job(job_id)
            self._require_terminal(current)
            items = self._services.database.candidates.accepted_items(job_id)
            if len(items) != _safe_int(current.get("total_items"), minimum=0):
                raise HTTPException(
                    status_code=409,
                    detail="请先为每一段选择最终采用的克隆候选",
                )
            self._validate_accepted_items(current, items)
            archive_path = self._archive_path(job_id, "-accepted.zip")
            with self._services.export_lock:
                self._build_accepted_archive(archive_path, current, items)
                response = self._snapshot_response(
                    archive_path,
                    f"{safe_filename(display_name)}_正式采用_{job['id']}.zip",
                )
        return response

    def script_archive_response(
        self, script: dict[str, Any], scope: str
    ) -> FileResponse:
        if scope not in {"accepted", "all"}:
            raise HTTPException(status_code=422, detail="导出范围必须是 accepted 或 all")
        script_id = str(script["id"])
        with self._services.job_mutation_lock:
            current = self._services.database.scripts.get(script_id)
            if not current:
                raise HTTPException(status_code=404, detail="找不到台本")
            if self._services.database.jobs.has_active_for_script(script_id):
                raise HTTPException(status_code=409, detail="请等待当前生成任务完成后再导出")
            selections = {}
            for row in self._services.database.selections.list_for_script(script_id):
                selections[_safe_int(row.get("sequence"), minimum=1)] = row
            candidates = self._services.database.candidates.list_for_script(script_id)
            candidate_by_id = {str(row["id"]): row for row in candidates}
            if scope == "accepted":
                self._validate_script_selections(current, selections, candidate_by_id)
                archive_path = self._archive_path(script_id, "-accepted.zip")
                filename = f"{safe_filename(str(current['name']))}_已采纳.zip"
            else:
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.get("status") == "completed"
                    and (candidate.get("raw_audio_path") or candidate.get("audio_path"))
                ]
                if not candidates:
                    raise HTTPException(status_code=409, detail="台本尚无可导出的历史音频")
                archive_path = self._archive_path(script_id, "-all.zip")
                filename = f"{safe_filename(str(current['name']))}_全部历史.zip"
            with self._services.export_lock:
                self._build_script_archive(
                    archive_path,
                    current,
                    scope,
                    selections,
                    candidates,
                    candidate_by_id,
                )
                response = self._snapshot_response(archive_path, filename)
        return response

    def sequence_filename(self, record: dict[str, Any]) -> str:
        sequence = _safe_int(record.get("sequence"), minimum=1)
        return f"{sequence:03d}.wav"

    def _archive_path(self, identifier: str, suffix: str) -> Path:
        if not _ARCHIVE_COMPONENT_RE.fullmatch(str(identifier)):
            raise HTTPException(status_code=409, detail=_CORRUPT_RECORD)
        try:
            return ensure_within(
                self._services.settings.export_root / f"{identifier}{suffix}",
                self._services.settings.root,
            )
        except ValueError:
            raise HTTPException(status_code=409, detail=_CORRUPT_RECORD) from None

    @staticmethod
    def _snapshot_response(archive_path: Path, filename: str) -> FileResponse:
        snapshot = archive_path.with_name(
            f"{DOWNLOAD_SNAPSHOT_PREFIX}{uuid.uuid4().hex}.zip"
        )
        try:
            shutil.copyfile(archive_path, snapshot)
        except OSError as error:
            snapshot.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="导出文件暂时不可用") from error
        return FileResponse(
            snapshot,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(_remove_snapshot, snapshot),
        )

    def audio_response(
        self, source_path: Path, filename: str | None = None
    ) -> FileResponse:
        snapshot = self._services.settings.export_root / (
            f"{DOWNLOAD_SNAPSHOT_PREFIX}{uuid.uuid4().hex}"
            f"{source_path.suffix or '.bin'}"
        )
        try:
            shutil.copyfile(source_path, snapshot)
        except OSError as error:
            snapshot.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="音频文件暂时不可用") from error
        return FileResponse(
            snapshot,
            media_type="audio/wav",
            filename=filename,
            background=BackgroundTask(_remove_snapshot, snapshot),
        )

    def _downloadable_items(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        self._require_terminal(job, detail="任务结束后才能全部下载")
        items = [
            item
            for item in self._services.database.jobs.items(str(job["id"]))
            if item.get("raw_audio_path") or item.get("audio_path")
        ]
        if not items:
            raise HTTPException(status_code=409, detail="任务尚无可下载音频")
        return items

    def _clone_path(self, item: dict[str, Any], missing_message: str) -> Path:
        if not any(
            str(item.get(field) or "").strip()
            for field in ("raw_audio_path", "audio_path")
        ):
            raise HTTPException(status_code=404, detail=missing_message)
        path = resolve_audio_path(item, self._services.settings.root)
        if path is None:
            raise HTTPException(status_code=404, detail="服务器上的音频文件已不存在")
        return path

    def _current_job(self, job_id: str) -> dict[str, Any]:
        current = self._services.database.jobs.get(job_id)
        if not current:
            raise HTTPException(status_code=404, detail="找不到任务")
        return current

    @staticmethod
    def _require_terminal(job: dict[str, Any], detail: str = "任务结束后才能导出") -> None:
        if job.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail=detail)

    def _validate_accepted_items(
        self, job: dict[str, Any], items: list[dict[str, Any]]
    ) -> None:
        expected = set(range(1, _safe_int(job.get("total_items"), minimum=0) + 1))
        if {
            _safe_int(item.get("sequence"), minimum=1) for item in items
        } != expected or any(
            item.get("status") != "completed"
            or not item.get("raw_audio_path")
            and not item.get("audio_path")
            for item in items
        ):
            raise HTTPException(status_code=409, detail="已采纳候选记录无效，不能导出")

    def _validate_script_selections(
        self,
        script: dict[str, Any],
        selections: dict[int, dict[str, Any]],
        candidate_by_id: dict[str, dict[str, Any]],
    ) -> None:
        expected = set(range(1, _safe_int(script.get("item_count"), minimum=0) + 1))
        missing = expected - set(selections)
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"还有 {len(missing)} 行未采纳，不能导出正式音频",
            )
        if set(selections) - expected:
            raise HTTPException(status_code=409, detail="台本采纳记录与当前台词不一致")
        for sequence, selection in selections.items():
            candidate = candidate_by_id.get(str(selection.get("candidate_id") or ""))
            if (
                not candidate
                or candidate.get("kind") != "gpt"
                or candidate.get("status") != "completed"
                or not (candidate.get("raw_audio_path") or candidate.get("audio_path"))
                or _safe_int(candidate.get("sequence"), minimum=1) != sequence
                or str(candidate.get("job_id")) != str(selection.get("job_id"))
                or str(candidate.get("job_item_id"))
                != str(selection.get("job_item_id"))
            ):
                raise HTTPException(status_code=409, detail="已采纳候选记录无效，不能导出")

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
                    path = self._clone_path(item, "采用的音频已不存在")
                    sequence = _safe_int(item.get("sequence"), minimum=1)
                    archive_name = f"Audio/{sequence:03d}.wav"
                    archive.write(path, archive_name)
                    manifest.append(
                        {
                            "id": f"{job['id']}_{sequence:03d}",
                            "sequence": sequence,
                            "text": _required_text(item, "text"),
                            "pronunciation": _required_text(item, "pronunciation"),
                            "rewriteInstruction": str(
                                item.get("rewrite_instruction") or ""
                            ),
                            "direction": _required_text(item, "direction"),
                            "candidateId": _required_text(item, "id"),
                            "audio": archive_name,
                            "durationSeconds": item.get("duration_seconds"),
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

    def _build_script_archive(
        self,
        archive_path: Path,
        script: dict[str, Any],
        scope: str,
        selections: dict[int, dict[str, Any]],
        candidates: list[dict[str, Any]],
        candidate_by_id: dict[str, dict[str, Any]],
    ) -> None:
        temporary_path = archive_path.with_suffix(".zip.tmp")
        try:
            manifest: list[dict[str, Any]] = []
            with zipfile.ZipFile(
                temporary_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                if scope == "accepted":
                    rows = [
                        candidate_by_id[str(selections[sequence]["candidate_id"])]
                        for sequence in sorted(selections)
                    ]
                else:
                    rows = [
                        candidate
                        for candidate in candidates
                        if candidate.get("raw_audio_path")
                        or candidate.get("audio_path")
                    ]
                for candidate in rows:
                    path = self._clone_path(candidate, "历史音频已不存在")
                    sequence = _safe_int(candidate.get("sequence"), minimum=1)
                    job_component = _archive_component(candidate.get("job_id"))
                    candidate_component = _archive_component(candidate.get("id"))
                    if scope == "accepted":
                        archive_name = f"Audio/{sequence:03d}.wav"
                    else:
                        archive_name = (
                            f"History/{sequence:03d}/{job_component}/"
                            f"{candidate_component}.wav"
                        )
                    archive.write(path, archive_name)
                    selection = selections.get(sequence)
                    manifest.append(
                        {
                            "sequence": sequence,
                            "text": _required_text(candidate, "text"),
                            "pronunciation": _required_text(candidate, "pronunciation"),
                            "rewriteInstruction": str(
                                candidate.get("rewrite_instruction") or ""
                            ),
                            "candidateId": candidate_component,
                            "jobId": job_component,
                            "voiceId": _required_text(candidate, "voice_id"),
                            "voiceName": _required_text(candidate, "voice_name"),
                            "modelId": _required_text(candidate, "model_id"),
                            "audio": archive_name,
                            "status": _required_text(candidate, "status"),
                            "accepted": bool(
                                selection
                                and str(selection["candidate_id"])
                                == str(candidate["id"])
                            ),
                            "selectedBy": (
                                selection.get("selected_by_name") if selection else None
                            ),
                            "selectedAt": (
                                selection.get("selected_at") if selection else None
                            ),
                        }
                    )
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "version": 1,
                            "scope": scope,
                            "scriptId": script["id"],
                            "scriptName": script["name"],
                            "items": manifest,
                        },
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
        path = self._clone_path(item, "任务音频文件已不存在")
        archive_name = self.sequence_filename(item)
        archive.write(path, archive_name)
        manifest.append(
            {
                "sequence": _safe_int(item.get("sequence"), minimum=1),
                "text": _required_text(item, "text"),
                "pronunciation": _required_text(item, "pronunciation"),
                "rewriteInstruction": str(item.get("rewrite_instruction") or ""),
                "file": archive_name,
            }
        )


def _safe_int(value: Any, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=409, detail=_CORRUPT_RECORD)
    if isinstance(value, float) and not value.is_integer():
        raise HTTPException(status_code=409, detail=_CORRUPT_RECORD)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=409, detail=_CORRUPT_RECORD) from None
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise HTTPException(status_code=409, detail=_CORRUPT_RECORD)
    return parsed


def _required_text(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if value is None or not str(value).strip():
        raise HTTPException(status_code=409, detail=_CORRUPT_RECORD)
    return str(value)


def _archive_component(value: Any) -> str:
    component = str(value or "")
    if not _ARCHIVE_COMPONENT_RE.fullmatch(component):
        raise HTTPException(status_code=409, detail=_CORRUPT_RECORD)
    return component


def _remove_snapshot(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
