from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..access import project_job, resolve_project_id
from ..audit import record_action
from ..candidate_operations import (
    accept_candidate,
    candidate_audio_response,
    regenerate_candidate,
)
from ..cleanup import remove_item_artifacts, remove_job_artifacts, remove_job_exports
from ..dependencies import CurrentUser, ServicesDep
from ..downloads import JobDownloadService
from ..job_creation import JobCreationService
from ..payloads import JobPresenter
from ..schemas import CandidateAccept, CandidateRegenerate, JobRename


router = APIRouter(prefix="/api/jobs")
API_PREFIX = "/api/jobs"


@router.post("", status_code=201)
def create_job(
    model_id: Annotated[str, Form(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    # `voice_id` remains accepted for single-voice clients.
    voice_id: Annotated[str, Form()] = "",
    voice_ids: Annotated[str, Form()] = "",
    script_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    candidate_count: Annotated[int, Form()] = 2,
    line_number: Annotated[int | None, Form()] = None,
    reference_emotion: Annotated[str, Form()] = "all",
    generation_settings: Annotated[str, Form()] = "",
    base_seed: Annotated[int | None, Form()] = None,
    model_ids: Annotated[str, Form()] = "",
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    jobs = JobCreationService(services, str(user["id"]), selected).create(
        requested_voice_id=voice_id,
        voice_ids_json=voice_ids,
        model_id=model_id,
        model_ids_json=model_ids,
        script_id=script_id,
        name=name,
        candidate_count=candidate_count,
        line_number=line_number,
        reference_emotion=reference_emotion,
        generation_settings_json=generation_settings,
        base_seed=base_seed,
    )
    for job in jobs:
        record_action(
            services,
            request,
            user,
            "job.created",
            target_type="job",
            target_id=str(job["id"]),
            project_id=selected,
            details={"model_id": job["model_id"], "name": job["name"]},
        )
    return {"job": jobs[0], "jobs": jobs}


@router.get("")
def list_jobs(
    user: CurrentUser,
    services: ServicesDep,
    limit: int = 100,
    project_id: str = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    presenter = JobPresenter(services)
    jobs = services.database.jobs.list_for_project(selected, _limit(limit))
    return {"jobs": presenter.payload_many(jobs)}


@router.get("/{job_id}")
def get_job(job_id: str, user: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    job = project_job(services, job_id, user)
    return {"job": JobPresenter(services).payload(job, include_items=True)}


@router.patch("/{job_id}")
def rename_job(
    job_id: str,
    changes: JobRename,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    name = _job_name(changes.name)
    current = project_job(services, job_id, user)
    if not services.database.jobs.rename(job_id, name):
        raise HTTPException(status_code=404, detail="找不到任务")
    job = project_job(services, job_id, user)
    record_action(
        services,
        request,
        user,
        "job.renamed",
        target_type="job",
        target_id=job_id,
        project_id=str(current["project_id"]),
        details={"name": name},
    )
    return {"job": JobPresenter(services).payload(job)}


@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> Response:
    job = project_job(services, job_id, user)
    if job["status"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="任务处理完成后才能删除")
    if not services.database.jobs.delete(job_id):
        raise HTTPException(status_code=404, detail="找不到任务")
    remove_job_artifacts(services, job_id)
    record_action(
        services,
        request,
        user,
        "job.deleted",
        target_type="job",
        target_id=job_id,
        project_id=str(job["project_id"]),
        details={"name": job.get("display_name") or job.get("script_name") or ""},
    )
    return Response(status_code=204)


@router.delete("/{job_id}/items/{item_id}", status_code=204)
def delete_item(
    job_id: str,
    item_id: str,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> Response:
    job = project_job(services, job_id, user)
    if job["status"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="任务处理完成后才能删除单条音频")
    item = services.database.jobs.item(job_id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="找不到该段音频")
    candidates = services.database.candidates.list_for_item(item_id)
    if item["status"] in {"queued", "running"} or any(
        candidate["status"] in {"queued", "running"} for candidate in candidates
    ):
        raise HTTPException(status_code=409, detail="该段音频仍在生成，完成后才能删除")
    deleted_item, deleted_candidates, job_deleted = services.database.jobs.delete_item(
        job_id, item_id
    )
    if not deleted_item:  # pragma: no cover - the item was checked above
        raise HTTPException(status_code=404, detail="找不到该段音频")
    remove_item_artifacts(services, deleted_item, deleted_candidates)
    if job_deleted:
        remove_job_artifacts(services, job_id)
    else:
        remove_job_exports(services, job_id)
    record_action(
        services,
        request,
        user,
        "job.item_deleted",
        target_type="job_item",
        target_id=item_id,
        project_id=str(job["project_id"]),
        details={"job_id": job_id, "sequence": int(item["sequence"])},
    )
    return Response(status_code=204)


@router.post("/{job_id}/items/{item_id}/regenerate", status_code=201)
def regenerate_item(
    job_id: str,
    item_id: str,
    changes: CandidateRegenerate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    job = project_job(services, job_id, user)
    candidate = regenerate_candidate(services, job, item_id, changes, API_PREFIX)
    record_action(
        services,
        request,
        user,
        "job.candidate_regenerated",
        target_type="candidate",
        target_id=str(candidate["id"]),
        project_id=str(job["project_id"]),
        details={"job_id": job_id, "item_id": item_id},
    )
    return {"candidate": candidate}


@router.post("/{job_id}/items/{item_id}/accept")
def adopt_item_candidate(
    job_id: str,
    item_id: str,
    changes: CandidateAccept,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    job = project_job(services, job_id, user)
    accept_candidate(services, job, item_id, changes.candidate_id)
    refreshed = project_job(services, job_id, user)
    record_action(
        services,
        request,
        user,
        "job.candidate_accepted",
        target_type="candidate",
        target_id=changes.candidate_id,
        project_id=str(job["project_id"]),
        details={"job_id": job_id, "item_id": item_id},
    )
    return {"job": JobPresenter(services).payload(refreshed, include_items=True)}


@router.get("/{job_id}/items/{item_id}/candidates/{candidate_id}/audio")
def play_candidate(
    job_id: str,
    item_id: str,
    candidate_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> FileResponse:
    job = project_job(services, job_id, user)
    return candidate_audio_response(services, job, item_id, candidate_id)


@router.get("/{job_id}/items/{item_id}/candidates/{candidate_id}/download")
def download_candidate(
    job_id: str,
    item_id: str,
    candidate_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> FileResponse:
    job = project_job(services, job_id, user)
    candidate, path = JobDownloadService(services).candidate_path(
        job, item_id, candidate_id
    )
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{int(candidate['sequence']):03d}.wav",
    )


@router.get("/{job_id}/items/{item_id}/audio")
def play_item(
    job_id: str, item_id: str, user: CurrentUser, services: ServicesDep
) -> FileResponse:
    job = project_job(services, job_id, user)
    _, path = JobDownloadService(services).item_path(job, item_id)
    return FileResponse(path, media_type="audio/wav")


@router.get("/{job_id}/items/{item_id}/download")
def download_item(
    job_id: str, item_id: str, user: CurrentUser, services: ServicesDep
) -> FileResponse:
    job = project_job(services, job_id, user)
    item, path = JobDownloadService(services).item_path(job, item_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{int(item['sequence']):03d}.wav",
    )


@router.get("/{job_id}/download")
def download_all(job_id: str, user: CurrentUser, services: ServicesDep) -> FileResponse:
    job = project_job(services, job_id, user)
    name = JobPresenter(services).payload(job)["name"]
    return JobDownloadService(services).archive_response(job, str(name))


@router.get("/{job_id}/export")
def export_accepted(
    job_id: str, user: CurrentUser, services: ServicesDep
) -> FileResponse:
    job = project_job(services, job_id, user)
    name = JobPresenter(services).payload(job)["name"]
    return JobDownloadService(services).accepted_archive_response(job, str(name))


def _job_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 80:
        raise HTTPException(status_code=422, detail="任务名称需为 1-80 个字符")
    return name


def _limit(value: int) -> int:
    return min(max(value, 1), 300)
