from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ...audio_quality import AudioQualityAnalyzer
from ...reference_emotions import validate_emotion
from ...storage import ensure_within
from ..downloads import JobDownloadService
from ..access import project_voice, resolve_project_id
from ..audit import record_action
from ..dependencies import CurrentUser, ServicesDep
from ..payloads import voice_payload
from ..schemas import VoiceFileUpdate, VoiceUpdate
from ..uploads import VoiceFileStorage


router = APIRouter(prefix="/api/voices")
logger = logging.getLogger(__name__)


@router.get("")
def list_voices(
    user: CurrentUser, services: ServicesDep, project_id: str = ""
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    return {
        "voices": [
            voice_payload(voice, True)
            for voice in services.database.voices.list(selected)
        ]
    }


@router.post("", status_code=201)
async def create_voice(
    name: Annotated[str, Form(...)],
    files: Annotated[list[UploadFile], File(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    notes: Annotated[str, Form()] = "",
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    clean_name, clean_notes = _voice_fields(name, notes)
    voice_id = services.database.voices.create(
        clean_name,
        str(user["id"]),
        clean_notes,
        project_id=selected,
    )
    try:
        await VoiceFileStorage(services).store(voice_id, files, selected)
    except Exception:
        services.database.voices.delete_empty(voice_id)
        _remove_empty_directory(
            services.settings.voice_upload_root / selected / voice_id
        )
        raise
    voice = services.database.voices.get(voice_id)
    record_action(
        services,
        request,
        user,
        "voice.created",
        target_type="voice",
        target_id=voice_id,
        project_id=selected,
        details={"name": clean_name, "file_count": len(files)},
    )
    return {"voice": voice_payload(voice, True) if voice else None}


@router.get("/{voice_id}")
def get_voice(
    voice_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    voice = project_voice(services, voice_id, user)
    files = _voice_files(services, voice_id)
    return {
        "voice": voice_payload(
            voice,
            True,
            files,
        )
    }


@router.patch("/{voice_id}")
def update_voice(
    voice_id: str,
    changes: VoiceUpdate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    voice = _editable_voice(services, voice_id, user)
    clean_name, clean_notes = _voice_fields(changes.name, changes.notes)
    services.database.voices.update(voice_id, clean_name, clean_notes)
    record_action(
        services,
        request,
        user,
        "voice.updated",
        target_type="voice",
        target_id=voice_id,
        project_id=str(voice["project_id"]),
        details={"name": clean_name},
    )
    return {"voice": _voice_detail(services, voice_id)}


@router.post("/{voice_id}/files", status_code=201)
async def add_voice_files(
    voice_id: str,
    files: Annotated[list[UploadFile], File(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    voice = _editable_voice(services, voice_id, user)
    await VoiceFileStorage(services).store(voice_id, files, str(voice["project_id"]))
    record_action(
        services,
        request,
        user,
        "voice.files_added",
        target_type="voice",
        target_id=voice_id,
        project_id=str(voice["project_id"]),
        details={"file_count": len(files)},
    )
    return {"voice": _voice_detail(services, voice_id)}


@router.patch("/{voice_id}/files/{file_id}")
def update_voice_file(
    voice_id: str,
    file_id: str,
    changes: VoiceFileUpdate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    voice = _editable_voice(services, voice_id, user)
    file_row = services.database.voices.get_file(file_id)
    if not file_row or str(file_row["voice_id"]) != voice_id:
        raise HTTPException(status_code=404, detail="找不到声音录音")
    if (
        changes.enabled is None
        and changes.emotion_tag is None
        and changes.reference_text is None
    ):
        raise HTTPException(status_code=422, detail="请至少修改启用状态、参考语气或参考文本")
    emotion: str | None = None
    if changes.emotion_tag is not None:
        try:
            emotion = validate_emotion(changes.emotion_tag)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    reference_text = (
        changes.reference_text.strip() if changes.reference_text is not None else None
    )
    try:
        updated = services.database.voices.update_file(
            file_id,
            enabled=changes.enabled,
            emotion_tag=emotion,
            reference_text=reference_text,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not updated:  # pragma: no cover - guarded by the lookup above
        raise HTTPException(status_code=404, detail="找不到声音录音")
    changed_fields = [
        field
        for field, value in (
            ("enabled", changes.enabled),
            ("emotion_tag", changes.emotion_tag),
            ("reference_text", changes.reference_text),
        )
        if value is not None
    ]
    record_action(
        services,
        request,
        user,
        "voice.file_updated",
        target_type="voice_file",
        target_id=file_id,
        project_id=str(voice["project_id"]),
        details={"fields": changed_fields},
    )
    return {"voice": _voice_detail(services, voice_id)}


@router.get("/{voice_id}/files/{file_id}/audio")
def play_voice_file(
    voice_id: str, file_id: str, user: CurrentUser, services: ServicesDep
) -> FileResponse:
    project_voice(services, voice_id, user)
    file_row = services.database.voices.get_file(file_id)
    if not file_row or str(file_row["voice_id"]) != voice_id:
        raise HTTPException(status_code=404, detail="找不到声音录音")
    try:
        path = ensure_within(Path(str(file_row["source_path"])), services.settings.root)
    except ValueError:
        logger.warning(
            "Voice file path rejected for file %s", str(file_row.get("id") or file_id)
        )
        raise HTTPException(status_code=404, detail="服务器上的录音文件不可用") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="服务器上的录音文件已不存在")
    return JobDownloadService(services).audio_response(path)


def _editable_voice(
    services: ServicesDep,
    voice_id: str,
    user: dict[str, Any],
) -> dict[str, Any]:
    return project_voice(services, voice_id, user)


def _voice_detail(services: ServicesDep, voice_id: str) -> dict[str, Any] | None:
    voice = services.database.voices.get(voice_id)
    files = _voice_files(services, voice_id)
    return voice_payload(voice, True, files) if voice else None


def _voice_files(services: ServicesDep, voice_id: str) -> list[dict[str, Any]]:
    files = services.database.voices.list_files(voice_id, enabled_only=False)
    for item in files:
        try:
            stored = json.loads(str(item.get("quality_json") or "{}"))
        except json.JSONDecodeError:
            stored = {}
        if stored:
            continue
        try:
            path = ensure_within(Path(str(item["source_path"])), services.settings.root)
            quality = AudioQualityAnalyzer().analyze(path)
        except Exception as error:
            logger.warning(
                "Voice quality analysis failed for file %s (%s)",
                str(item.get("id") or ""),
                type(error).__name__,
            )
            quality = _quality_failure()
        services.database.voices.update_file_quality(str(item["id"]), quality)
        item["quality_json"] = json.dumps(
            quality, ensure_ascii=False, separators=(",", ":")
        )
    return files


def _quality_failure() -> dict[str, Any]:
    return {
        "score": 0,
        "grade": "poor",
        "issues": [
            {
                "code": "analysis",
                "label": "检测失败",
                "message": "音频质量分析失败",
            }
        ],
    }


def _voice_fields(name: str, notes: str) -> tuple[str, str]:
    clean_name = name.strip()
    clean_notes = notes.strip()
    if not clean_name or len(clean_name) > 80:
        raise HTTPException(status_code=422, detail="声音库名称需为 1-80 个字符")
    if len(clean_notes) > 500:
        raise HTTPException(status_code=422, detail="备注不能超过 500 个字符")
    return clean_name, clean_notes


def _remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass
