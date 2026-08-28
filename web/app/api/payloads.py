from __future__ import annotations

import json
from typing import Any

from voice_core.pronunciation import is_raw_pronunciation

from ..domain import ScriptItem
from ..generation_settings import stored_generation_settings
from ..services import ApplicationServices
from ..search import search_text
from ..value_utils import stored_int


SCRIPT_FIELDS = (
    "id",
    "name",
    "original_name",
    "owner_id",
    "project_id",
    "source_kind",
    "prompt",
    "item_count",
    "version",
    "created_at",
)


def script_payload(script: dict[str, Any]) -> dict[str, Any]:
    return {
        **{field: script.get(field, "") for field in SCRIPT_FIELDS},
        "search_text": search_text(script["name"], script["original_name"]),
    }


def script_detail_payload(
    script: dict[str, Any], items: list[ScriptItem]
) -> dict[str, Any]:
    return {
        **script_payload(script),
        "items": [item.as_dict() for item in items],
    }


def voice_payload(
    voice: dict[str, Any],
    can_edit: bool,
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = _voice_summary(voice, can_edit)
    if files is not None:
        payload["files"] = [_voice_file_payload(voice, item) for item in files]
    return payload


def _voice_summary(voice: dict[str, Any], can_edit: bool) -> dict[str, Any]:
    return {
        "id": voice["id"],
        "name": voice["name"],
        "owner_id": voice["owner_id"],
        "project_id": voice.get("project_id") or "",
        "source_kind": voice["source_kind"],
        "notes": voice["notes"],
        "file_count": stored_int(voice.get("file_count")),
        "enabled_file_count": stored_int(voice.get("enabled_file_count")),
        "size_bytes": stored_int(voice.get("size_bytes")),
        "created_at": voice["created_at"],
        "can_edit": can_edit,
        "search_text": search_text(voice["name"], voice["notes"]),
    }


def _voice_file_payload(voice: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "original_name": item["original_name"],
        "size_bytes": stored_int(item.get("size_bytes")),
        "enabled": bool(item["enabled"]),
        "emotion_tag": item.get("emotion_tag") or "neutral",
        "reference_text": item.get("reference_text") or "",
        "quality": _json_object(item.get("quality_json")),
        "created_at": item["created_at"],
        "audio_url": f"/api/voices/{voice['id']}/files/{item['id']}/audio",
    }


def candidate_payload(
    candidate: dict[str, Any], api_prefix: str = "/api/jobs"
) -> dict[str, Any]:
    base_url = (
        f"{api_prefix}/{candidate['job_id']}/items/{candidate['job_item_id']}"
        f"/candidates/{candidate['id']}"
    )
    audio = bool(candidate.get("raw_audio_path") or candidate.get("audio_path"))
    return {
        "id": candidate["id"],
        "job_item_id": candidate["job_item_id"],
        "source_candidate_id": candidate.get("source_candidate_id"),
        "origin_type": candidate["origin_type"],
        "name": candidate["name"],
        "ordinal": candidate["ordinal"],
        "seed": candidate.get("seed"),
        "text": candidate["text"],
        "pronunciation": candidate["pronunciation"],
        "generated_text": candidate["generated_text"],
        "raw_mode": is_raw_pronunciation(str(candidate.get("pronunciation") or "")),
        "direction": candidate["direction"],
        "emphasis": [
            value for value in str(candidate.get("emphasis") or "").split(",") if value
        ],
        "generation_settings": stored_generation_settings(
            candidate.get("generation_settings_json")
        ),
        "status": candidate["status"],
        "accepted": candidate.get("accepted_candidate_id") == candidate["id"],
        "duration_seconds": candidate.get("duration_seconds"),
        "processing_backend": candidate.get("processing_backend") or "",
        "submitted_at": candidate.get("submitted_at"),
        "error": candidate.get("error") or "",
        "audio_url": f"{base_url}/audio" if audio else None,
        "download_url": f"{base_url}/download" if audio else None,
    }


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class JobPresenter:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services

    def payload(
        self,
        job: dict[str, Any],
        include_items: bool = False,
        api_prefix: str = "/api/jobs",
        queue_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._summary(job)
        payload.update(self._queue(job, queue_snapshot))
        if include_items:
            self._add_detail(payload, job, api_prefix)
        return payload

    def payload_many(
        self,
        jobs: list[dict[str, Any]],
        api_prefix: str = "/api/jobs",
    ) -> list[dict[str, Any]]:
        snapshots = self._services.database.monitoring.queue_snapshots(jobs)
        return [
            self.payload(
                job,
                api_prefix=api_prefix,
                queue_snapshot=snapshots.get(str(job["id"])),
            )
            for job in jobs
        ]

    def _add_detail(
        self,
        payload: dict[str, Any],
        job: dict[str, Any],
        api_prefix: str,
    ) -> None:
        items = self._services.database.jobs.items(str(job["id"]))
        candidates_by_item: dict[str, list[dict[str, Any]]] = {}
        for candidate in self._services.database.candidates.list_for_job(
            str(job["id"])
        ):
            if candidate["kind"] != "gpt":
                continue
            candidates_by_item.setdefault(str(candidate["job_item_id"]), []).append(
                candidate
            )
        base_url = f"{api_prefix}/{job['id']}"
        payload["items"] = [
            self._item_payload(
                item,
                base_url,
                candidates_by_item.get(str(item["id"]), []),
                api_prefix,
            )
            for item in items
        ]
        payload["download_url"] = f"{base_url}/download"
        payload["export_url"] = f"{base_url}/export"
        payload["accepted_items"] = sum(
            any(candidate["accepted"] for candidate in item["candidates"])
            for item in payload["items"]
        )
        payload["can_export"] = bool(items) and payload["accepted_items"] == len(items)

    def _summary(self, job: dict[str, Any]) -> dict[str, Any]:
        total = stored_int(job.get("total_items"))
        completed = min(stored_int(job.get("completed_items")), total)
        model_id = str(job["model_id"])
        model_label = self._model_label(model_id)
        return {
            "id": job["id"],
            "name": str(job.get("display_name") or "").strip() or job["script_name"],
            "client_id": job["client_id"],
            "project_id": job.get("project_id") or "",
            "project_name": job.get("project_name") or job.get("project_id") or "",
            "created_by": job.get("created_by") or job["client_id"],
            "script_id": job["script_id"],
            "script_name": job["script_name"],
            "voice_id": job["voice_id"],
            "voice_name": job["voice_name"],
            "model_id": model_id,
            "model_label": model_label,
            "output_type": self._output_type(model_id),
            "candidate_count": max(1, stored_int(job.get("candidate_count"), 1)),
            "reference_emotion": job.get("reference_emotion") or "all",
            "status": job["status"],
            "total_items": total,
            "completed_items": completed,
            "progress": round(completed * 100 / total) if total else 0,
            "submitted_at": job["submitted_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "eta_seconds": JobPresenter._eta(job),
            "error": job["error"],
            "search_text": search_text(
                job.get("display_name"),
                job.get("script_name"),
                job.get("voice_name"),
                job.get("project_name"),
                model_label,
                model_id,
            ),
        }

    def _model_label(self, model_id: str) -> str:
        try:
            profile = self._services.profiles.model(model_id)
            return str(profile.get("label") or model_id)
        except ValueError:
            return model_id

    def _output_type(self, model_id: str) -> str:
        try:
            engine = str(self._services.profiles.model(model_id)["engine"])
        except ValueError:
            return "gpt_sovits_raw"
        return "gpt_sovits_raw" if engine == "gpt_sovits_v2" else f"{engine}_raw"

    @staticmethod
    def _eta(job: dict[str, Any]) -> int | None:
        value = job["eta_seconds"]
        return max(0, stored_int(value)) if value is not None else None

    def _queue(
        self, job: dict[str, Any], snapshot: dict[str, Any] | None
    ) -> dict[str, Any]:
        if job["status"] in {"queued", "running"}:
            return snapshot or self._services.database.monitoring.queue_snapshot(
                str(job["id"])
            )
        return {
            "queue_position": None,
            "estimated_wait_seconds": 0,
            "estimated_item_seconds": None,
        }

    @staticmethod
    def _item_payload(
        item: dict[str, Any],
        base_url: str,
        candidates: list[dict[str, Any]],
        api_prefix: str,
    ) -> dict[str, Any]:
        audio = bool(item.get("raw_audio_path") or item.get("audio_path"))
        accepted_id = str(item.get("accepted_candidate_id") or "")
        visible_ids = {str(candidate["id"]) for candidate in candidates}
        return {
            "id": item["id"],
            "sequence": item["sequence"],
            "source_line": item["source_line"],
            "text": item["text"],
            "pronunciation": item["pronunciation"],
            "generated_text": item["generated_text"],
            "raw_mode": is_raw_pronunciation(str(item.get("pronunciation") or "")),
            "direction": item["direction"],
            "emphasis": [value for value in item["emphasis"].split(",") if value],
            "status": item["status"],
            "duration_seconds": item["duration_seconds"],
            "processing_backend": item["processing_backend"],
            "error": item["error"],
            "accepted_candidate_id": accepted_id
            if accepted_id in visible_ids
            else None,
            "candidates": [
                candidate_payload(candidate, api_prefix) for candidate in candidates
            ],
            "audio_url": f"{base_url}/items/{item['id']}/audio" if audio else None,
            "download_url": (
                f"{base_url}/items/{item['id']}/download" if audio else None
            ),
        }
