from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..access import AdminAccess, admin_job
from ..candidate_operations import (
    accept_candidate,
    candidate_audio_response,
    regenerate_candidate,
)
from ..audit import record_action
from ..cleanup import remove_job_artifacts, remove_script_exports
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
    with services.job_mutation_lock:
        if not services.database.jobs.rename(job_id, name):
            raise HTTPException(status_code=404, detail="找不到任务")
        job = admin_job(services, job_id)
    return {"job": JobPresenter(services).payload(job, api_prefix=API_PREFIX)}


@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    user: AdminAccess,
    services: ServicesDep,
    request: Request,
) -> Response:
    with services.job_mutation_lock:
        job = admin_job(services, job_id)
        if job["status"] in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务处理完成后才能删除")
        if services.database.jobs.has_active_candidates(job_id):
            raise HTTPException(status_code=409, detail="任务仍有候选在生成，完成后才能删除")
        if not services.database.jobs.delete(job_id):
            if services.database.jobs.get(job_id):
                raise HTTPException(status_code=409, detail="任务仍有候选在生成，完成后才能删除")
            raise HTTPException(status_code=404, detail="找不到任务")
        remove_job_artifacts(services, job_id)
        remove_script_exports(services, str(job["script_id"]))
    record_action(
        services,
        request,
        user,
        "job.deleted",
        target_type="job",
        target_id=job_id,
        project_id=str(job.get("project_id") or ""),
        details={"name": job.get("display_name") or job.get("script_name") or ""},
    )
    return Response(status_code=204)


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
    service = JobDownloadService(services)
    candidate, _ = service.candidate_path(job, item_id, candidate_id)
    return service.candidate_response(
        job,
        item_id,
        candidate_id,
        filename=service.sequence_filename(candidate),
    )


@router.get("/{job_id}/items/{item_id}/audio")
def play_item(
    job_id: str, item_id: str, _: AdminAccess, services: ServicesDep
) -> FileResponse:
    job = admin_job(services, job_id)
    return JobDownloadService(services).item_response(job, item_id)


@router.get("/{job_id}/items/{item_id}/download")
def download_item(
    job_id: str, item_id: str, _: AdminAccess, services: ServicesDep
) -> FileResponse:
    job = admin_job(services, job_id)
    service = JobDownloadService(services)
    item, _ = service.item_path(job, item_id)
    return service.item_response(job, item_id, filename=service.sequence_filename(item))


@router.get("/{job_id}/download")
def download_all(job_id: str, _: AdminAccess, services: ServicesDep) -> FileResponse:
    job = admin_job(services, job_id)
    name = str(job.get("display_name") or job.get("script_name") or job["id"])
    return JobDownloadService(services).archive_response(job, str(name))


@router.get("/{job_id}/export")
def export_accepted(job_id: str, _: AdminAccess, services: ServicesDep) -> FileResponse:
    job = admin_job(services, job_id)
    name = str(job.get("display_name") or job.get("script_name") or job["id"])
    return JobDownloadService(services).accepted_archive_response(job, str(name))
