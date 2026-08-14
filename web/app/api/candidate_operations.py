from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..domain import ScriptItem
from ..generation_settings import normalize_generation_settings
from ..script_parser import ScriptFormatError, analyze_script_pronunciation
from ..services import ApplicationServices
from ..storage import ensure_within
from .downloads import JobDownloadService
from .payloads import candidate_payload
from .schemas import CandidateRegenerate


def regenerate_candidate(
    services: ApplicationServices,
    job: dict[str, Any],
    item_id: str,
    request: CandidateRegenerate,
    api_prefix: str,
) -> dict[str, Any]:
    item = _job_item(services, job, item_id)
    _validate_name(request.name)
    script_item = _script_item(item, request)
    try:
        generation_settings = normalize_generation_settings(request.generation_settings)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    source_id = request.source_candidate_id or str(
        item.get("accepted_candidate_id") or ""
    )
    source = _optional_source(services, job, item, source_id)
    candidate_id = services.database.candidates.create_regeneration(
        job_item=item,
        script_item=script_item,
        seed=_seed(request.seed),
        generation_settings=generation_settings,
        name=request.name,
        source_candidate_id=str(source["id"]) if source else None,
    )
    candidate = services.database.candidates.get(candidate_id)
    if not candidate:  # pragma: no cover
        raise HTTPException(status_code=500, detail="逐句候选创建后未找到")
    services.job_queue.submit_candidate(candidate_id)
    return candidate_payload(candidate, api_prefix)


def accept_candidate(
    services: ApplicationServices,
    job: dict[str, Any],
    item_id: str,
    candidate_id: str,
) -> None:
    item = _job_item(services, job, item_id)
    candidate = _source(services, job, item, candidate_id)
    _validate_clone_audio(services, candidate)
    if not services.database.candidates.accept(item_id, candidate_id):
        raise HTTPException(status_code=409, detail="候选尚未完成，不能采用")
    (services.settings.export_root / f"{job['id']}-accepted.zip").unlink(
        missing_ok=True
    )


def candidate_audio_response(
    services: ApplicationServices,
    job: dict[str, Any],
    item_id: str,
    candidate_id: str,
) -> FileResponse:
    _, path = JobDownloadService(services).candidate_path(job, item_id, candidate_id)
    return FileResponse(path, media_type="audio/wav")


def _job_item(
    services: ApplicationServices,
    job: dict[str, Any],
    item_id: str,
) -> dict[str, Any]:
    item = services.database.jobs.item(str(job["id"]), item_id)
    if not item:
        raise HTTPException(status_code=404, detail="找不到台本段落")
    return item


def _optional_source(
    services: ApplicationServices,
    job: dict[str, Any],
    item: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any] | None:
    if not candidate_id:
        return None
    return _source(services, job, item, candidate_id)


def _source(
    services: ApplicationServices,
    job: dict[str, Any],
    item: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    candidate = services.database.candidates.get_for_job(str(job["id"]), candidate_id)
    if (
        not candidate
        or candidate["kind"] != "gpt"
        or str(candidate["job_item_id"]) != str(item["id"])
    ):
        raise HTTPException(status_code=404, detail="找不到该段克隆音频")
    return candidate


def _script_item(item: dict[str, Any], request: CandidateRegenerate) -> ScriptItem:
    text = request.text.strip()
    pronunciation = request.pronunciation.strip()
    if not text or len(text) > 1000:
        raise HTTPException(status_code=422, detail="正常台词需为 1-1000 个字符")
    if not pronunciation or len(pronunciation) > 1000:
        raise HTTPException(status_code=422, detail="发音标记需为 1-1000 个字符")
    try:
        analysis = analyze_script_pronunciation(pronunciation)
    except ScriptFormatError as error:
        raise HTTPException(status_code=422, detail=f"发音标记错误: {error}") from error
    direction = analysis.direction if request.direction == "auto" else request.direction
    generated = _directional_text(analysis.generated_text, direction)
    return ScriptItem(
        order=int(item["sequence"]),
        source_line=int(item["source_line"]),
        text=text,
        pronunciation=pronunciation,
        generated_text=generated,
        direction=direction,
        emphasis=analysis.emphasis,
        hold_units=analysis.hold_units,
    )


def _directional_text(value: str, direction: str) -> str:
    text = value.rstrip("。？！")
    if direction == "rise":
        return text + "？"
    if direction == "fall":
        return text + "。"
    return value


def _seed(value: int | None) -> int:
    if value is None:
        return secrets.randbelow(2_000_000_000) + 1
    if value < 0 or value > 2_147_483_647:
        raise HTTPException(status_code=422, detail="随机种子需在 0 到 2147483647 之间")
    return value


def _validate_name(value: str) -> None:
    if len(value.strip()) > 80:
        raise HTTPException(status_code=422, detail="候选名称不能超过 80 个字符")


def _validate_clone_audio(
    services: ApplicationServices, candidate: dict[str, Any]
) -> None:
    if candidate.get("status") != "completed":
        raise HTTPException(status_code=409, detail="候选尚未完成，不能采用")
    value = str(candidate.get("raw_audio_path") or candidate.get("audio_path") or "")
    if not value:
        raise HTTPException(status_code=409, detail="候选尚未完成，不能采用")
    try:
        path = ensure_within(Path(value), services.settings.root)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="克隆音频路径无效") from error
    if not path.is_file():
        raise HTTPException(status_code=409, detail="克隆音频已不存在")
