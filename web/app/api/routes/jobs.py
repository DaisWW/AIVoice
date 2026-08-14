from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ...domain import ScriptItem
from ...reference_emotions import validate_emotion
from ..access import owned_job
from ..candidate_operations import (
    accept_candidate,
    candidate_audio_response,
    regenerate_candidate,
)
from ..dependencies import ClientId, ServicesDep
from ..downloads import JobDownloadService
from ..payloads import JobPresenter
from ..schemas import CandidateAccept, CandidateRegenerate, JobRename
from ..uploads import ScriptStorage


router = APIRouter(prefix="/api/jobs")
API_PREFIX = "/api/jobs"


@router.post("", status_code=201)
async def create_job(
    voice_id: Annotated[str, Form(...)],
    model_id: Annotated[str, Form(...)],
    client_id: ClientId,
    services: ServicesDep,
    script_id: Annotated[str, Form()] = "",
    script: Annotated[UploadFile | None, File()] = None,
    name: Annotated[str, Form()] = "",
    candidate_count: Annotated[int, Form()] = 2,
    reference_emotion: Annotated[str, Form()] = "all",
) -> dict[str, Any]:
    try:
        reference_emotion = validate_emotion(reference_emotion, allow_all=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _validate_job(
        services,
        voice_id,
        model_id,
        name,
        candidate_count,
        reference_emotion,
    )
    selected_script_id, items = await _resolve_script(
        services, client_id, script_id, script
    )
    job_id = services.database.jobs.create(
        client_id,
        selected_script_id,
        voice_id,
        model_id,
        items,
        candidate_count=candidate_count,
        reference_emotion=reference_emotion,
    )
    if name.strip():
        services.database.jobs.rename(job_id, name.strip(), client_id)
    job = services.database.jobs.get(job_id)
    if not job:  # pragma: no cover
        raise HTTPException(status_code=500, detail="任务创建后未找到")
    payload = JobPresenter(services).payload(job)
    services.job_queue.submit(job_id)
    return {"job": payload}


@router.get("")
def list_jobs(
    client_id: ClientId, services: ServicesDep, limit: int = 100
) -> dict[str, Any]:
    presenter = JobPresenter(services)
    jobs = services.database.jobs.list_for_client(client_id, _limit(limit))
    return {"jobs": presenter.payload_many(jobs)}


@router.get("/{job_id}")
def get_job(job_id: str, client_id: ClientId, services: ServicesDep) -> dict[str, Any]:
    job = owned_job(services, job_id, client_id)
    return {"job": JobPresenter(services).payload(job, include_items=True)}


@router.patch("/{job_id}")
def rename_job(
    job_id: str,
    changes: JobRename,
    client_id: ClientId,
    services: ServicesDep,
) -> dict[str, Any]:
    name = _job_name(changes.name)
    if not services.database.jobs.rename(job_id, name, client_id):
        raise HTTPException(status_code=404, detail="找不到任务")
    job = owned_job(services, job_id, client_id)
    return {"job": JobPresenter(services).payload(job)}


@router.post("/{job_id}/items/{item_id}/regenerate", status_code=201)
def regenerate_item(
    job_id: str,
    item_id: str,
    changes: CandidateRegenerate,
    client_id: ClientId,
    services: ServicesDep,
) -> dict[str, Any]:
    job = owned_job(services, job_id, client_id)
    return {
        "candidate": regenerate_candidate(services, job, item_id, changes, API_PREFIX)
    }


@router.post("/{job_id}/items/{item_id}/accept")
def adopt_item_candidate(
    job_id: str,
    item_id: str,
    changes: CandidateAccept,
    client_id: ClientId,
    services: ServicesDep,
) -> dict[str, Any]:
    job = owned_job(services, job_id, client_id)
    accept_candidate(services, job, item_id, changes.candidate_id)
    refreshed = owned_job(services, job_id, client_id)
    return {"job": JobPresenter(services).payload(refreshed, include_items=True)}


@router.get("/{job_id}/items/{item_id}/candidates/{candidate_id}/audio")
def play_candidate(
    job_id: str,
    item_id: str,
    candidate_id: str,
    client_id: ClientId,
    services: ServicesDep,
) -> FileResponse:
    job = owned_job(services, job_id, client_id)
    return candidate_audio_response(services, job, item_id, candidate_id)


@router.get("/{job_id}/items/{item_id}/candidates/{candidate_id}/download")
def download_candidate(
    job_id: str,
    item_id: str,
    candidate_id: str,
    client_id: ClientId,
    services: ServicesDep,
) -> FileResponse:
    job = owned_job(services, job_id, client_id)
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
    job_id: str, item_id: str, client_id: ClientId, services: ServicesDep
) -> FileResponse:
    job = owned_job(services, job_id, client_id)
    _, path = JobDownloadService(services).item_path(job, item_id)
    return FileResponse(path, media_type="audio/wav")


@router.get("/{job_id}/items/{item_id}/download")
def download_item(
    job_id: str, item_id: str, client_id: ClientId, services: ServicesDep
) -> FileResponse:
    job = owned_job(services, job_id, client_id)
    item, path = JobDownloadService(services).item_path(job, item_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{int(item['sequence']):03d}.wav",
    )


@router.get("/{job_id}/download")
def download_all(
    job_id: str, client_id: ClientId, services: ServicesDep
) -> FileResponse:
    job = owned_job(services, job_id, client_id)
    name = JobPresenter(services).payload(job)["name"]
    return JobDownloadService(services).archive_response(job, str(name))


@router.get("/{job_id}/export")
def export_accepted(
    job_id: str, client_id: ClientId, services: ServicesDep
) -> FileResponse:
    job = owned_job(services, job_id, client_id)
    name = JobPresenter(services).payload(job)["name"]
    return JobDownloadService(services).accepted_archive_response(job, str(name))


def _validate_job(
    services: ServicesDep,
    voice_id: str,
    model_id: str,
    name: str,
    candidate_count: int,
    reference_emotion: str,
) -> None:
    if not services.database.voices.get(voice_id):
        raise HTTPException(status_code=404, detail="找不到所选声音库")
    if not services.database.voices.list_files(voice_id):
        raise HTTPException(status_code=422, detail="所选声音库没有启用的录音")
    if len(name.strip()) > 80:
        raise HTTPException(status_code=422, detail="任务名称不能超过 80 个字符")
    if candidate_count not in {2, 3}:
        raise HTTPException(status_code=422, detail="每句候选数量只能选择 2 或 3")
    if reference_emotion != "all":
        files = services.database.voices.list_files(voice_id)
        if not any(item.get("emotion_tag") == reference_emotion for item in files):
            raise HTTPException(
                status_code=422,
                detail="所选声音库没有启用该语气分组的录音",
            )
    try:
        services.profiles.model(model_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


async def _resolve_script(
    services: ServicesDep,
    client_id: str,
    script_id: str,
    upload: UploadFile | None,
) -> tuple[str, list[ScriptItem]]:
    storage = ScriptStorage(services)
    if upload and upload.filename:
        return await storage.store(upload, client_id)
    if script_id.strip():
        script, items = storage.read(script_id.strip())
        return str(script["id"]), items
    raise HTTPException(status_code=422, detail="请选择已有台本，或上传一份新台本")


def _job_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 80:
        raise HTTPException(status_code=422, detail="任务名称需为 1-80 个字符")
    return name


def _limit(value: int) -> int:
    return min(max(value, 1), 300)
