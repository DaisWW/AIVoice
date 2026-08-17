from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse

from ..access import project_job, resolve_project_id
from ..audit import record_action
from ..candidate_operations import (
    accept_candidate,
    candidate_audio_response,
    regenerate_candidate,
)
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
    # Legacy clients may confirm the binding, but can never override it.
    voice_id: Annotated[str, Form()] = "",
    script_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    candidate_count: Annotated[int, Form()] = 2,
    reference_emotion: Annotated[str, Form()] = "all",
    generation_settings: Annotated[str, Form()] = "",
    base_seed: Annotated[int | None, Form()] = None,
    model_ids: Annotated[str, Form()] = "",
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    jobs = JobCreationService(services, str(user["id"]), selected).create(
        requested_voice_id=voice_id,
        model_id=model_id,
        model_ids_json=model_ids,
        script_id=script_id,
        name=name,
        candidate_count=candidate_count,
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
