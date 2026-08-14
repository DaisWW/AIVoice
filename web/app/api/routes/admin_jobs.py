from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..access import AdminAccess, admin_job
from ..candidate_operations import (
    accept_candidate,
    candidate_audio_response,
    regenerate_candidate,
)
from ..dependencies import ServicesDep
from ..downloads import JobDownloadService
from ..payloads import JobPresenter
from ..schemas import CandidateAccept, CandidateRegenerate, JobRename


router = APIRouter(prefix="/api/admin/jobs")
API_PREFIX = "/api/admin/jobs"


@router.get("")
def list_jobs(
    _: AdminAccess, services: ServicesDep, limit: int = 100
) -> dict[str, Any]:
    presenter = JobPresenter(services)
    jobs = services.database.jobs.list(min(max(limit, 1), 300))
    return {"jobs": presenter.payload_many(jobs, api_prefix=API_PREFIX)}


@router.get("/{job_id}")
def get_job(job_id: str, _: AdminAccess, services: ServicesDep) -> dict[str, Any]:
    return {
        "job": JobPresenter(services).payload(
            admin_job(services, job_id),
            include_items=True,
            api_prefix=API_PREFIX,
        )
    }


@router.patch("/{job_id}")
def rename_job(
    job_id: str,
    changes: JobRename,
    _: AdminAccess,
    services: ServicesDep,
) -> dict[str, Any]:
    name = changes.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(status_code=422, detail="任务名称需为 1-80 个字符")
    if not services.database.jobs.rename(job_id, name):
        raise HTTPException(status_code=404, detail="找不到任务")
    job = admin_job(services, job_id)
    return {"job": JobPresenter(services).payload(job, api_prefix=API_PREFIX)}


@router.post("/{job_id}/items/{item_id}/regenerate", status_code=201)
def regenerate_item(
    job_id: str,
    item_id: str,
    changes: CandidateRegenerate,
    _: AdminAccess,
    services: ServicesDep,
) -> dict[str, Any]:
    job = admin_job(services, job_id)
    return {
        "candidate": regenerate_candidate(services, job, item_id, changes, API_PREFIX)
    }


@router.post("/{job_id}/items/{item_id}/accept")
def adopt_item_candidate(
    job_id: str,
    item_id: str,
    changes: CandidateAccept,
    _: AdminAccess,
    services: ServicesDep,
) -> dict[str, Any]:
    job = admin_job(services, job_id)
    accept_candidate(services, job, item_id, changes.candidate_id)
    return {
        "job": JobPresenter(services).payload(
            admin_job(services, job_id),
            include_items=True,
            api_prefix=API_PREFIX,
        )
    }


@router.get("/{job_id}/items/{item_id}/candidates/{candidate_id}/audio")
def play_candidate(
    job_id: str,
    item_id: str,
    candidate_id: str,
    _: AdminAccess,
    services: ServicesDep,
) -> FileResponse:
    return candidate_audio_response(
        services, admin_job(services, job_id), item_id, candidate_id
    )


@router.get("/{job_id}/items/{item_id}/candidates/{candidate_id}/download")
def download_candidate(
    job_id: str,
    item_id: str,
    candidate_id: str,
    _: AdminAccess,
    services: ServicesDep,
) -> FileResponse:
    job = admin_job(services, job_id)
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
    job_id: str, item_id: str, _: AdminAccess, services: ServicesDep
) -> FileResponse:
    job = admin_job(services, job_id)
    _, path = JobDownloadService(services).item_path(job, item_id)
    return FileResponse(path, media_type="audio/wav")


@router.get("/{job_id}/items/{item_id}/download")
def download_item(
    job_id: str, item_id: str, _: AdminAccess, services: ServicesDep
) -> FileResponse:
    job = admin_job(services, job_id)
    item, path = JobDownloadService(services).item_path(job, item_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{int(item['sequence']):03d}.wav",
    )


@router.get("/{job_id}/download")
def download_all(job_id: str, _: AdminAccess, services: ServicesDep) -> FileResponse:
    job = admin_job(services, job_id)
    name = JobPresenter(services).payload(job, api_prefix=API_PREFIX)["name"]
    return JobDownloadService(services).archive_response(job, str(name))


@router.get("/{job_id}/export")
def export_accepted(job_id: str, _: AdminAccess, services: ServicesDep) -> FileResponse:
    job = admin_job(services, job_id)
    name = JobPresenter(services).payload(job, api_prefix=API_PREFIX)["name"]
    return JobDownloadService(services).accepted_archive_response(job, str(name))
