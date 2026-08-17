from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..access import project_script, project_voice, resolve_project_id
from ..audit import record_action
from ..dependencies import CurrentUser, ServicesDep
from ..payloads import script_detail_payload, script_payload
from ..schemas import ScriptUpdate
from ..uploads import ScriptStorage


router = APIRouter(prefix="/api/scripts")


@router.get("")
def list_scripts(
    user: CurrentUser, services: ServicesDep, project_id: str = ""
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    return {
        "scripts": [
            script_payload(script)
            for script in services.database.scripts.list(selected)
        ]
    }


@router.post("", status_code=201)
async def upload_script(
    file: Annotated[UploadFile, File(...)],
    default_voice_id: Annotated[str, Form(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    voice_id = _configured_voice_id(services, user, default_voice_id, selected)
    script_id, _ = await ScriptStorage(services).store(
        file,
        str(user["id"]),
        selected,
        default_voice_id=voice_id,
    )
    script = services.database.scripts.get(script_id)
    if not script:  # pragma: no cover - guarded by the insert above
        raise HTTPException(status_code=500, detail="台本上传后未找到")
    record_action(
        services,
        request,
        user,
        "script.uploaded",
        target_type="script",
        target_id=script_id,
        project_id=selected,
        details={"name": script["name"], "default_voice_id": voice_id},
    )
    return {"script": script_payload(script)}


@router.get("/{script_id}")
def get_script(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    items = ScriptStorage(services).load_items(script)
    return {"script": script_detail_payload(script, items)}


@router.patch("/{script_id}")
def update_script(
    script_id: str,
    changes: ScriptUpdate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    voice_id = _configured_voice_id(
        services,
        user,
        changes.default_voice_id,
        str(script.get("project_id") or ""),
    )
    name = changes.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="台本名称不能为空")
    if not services.database.scripts.update(script_id, name, voice_id):
        raise HTTPException(status_code=404, detail="找不到台本")
    updated = services.database.scripts.get(script_id)
    if not updated:  # pragma: no cover - guarded by the update above
        raise HTTPException(status_code=500, detail="台本更新后未找到")
    record_action(
        services,
        request,
        user,
        "script.updated",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"name": name, "default_voice_id": voice_id},
    )
    return {"script": script_payload(updated)}


def _configured_voice_id(
    services: ServicesDep,
    user: dict[str, Any],
    value: str,
    project_id: str,
) -> str:
    voice_id = value.strip()
    if not voice_id:
        raise HTTPException(status_code=422, detail="请选择声音库")
    voice = project_voice(services, voice_id, user)
    if str(voice.get("project_id") or "") != project_id:
        raise HTTPException(status_code=404, detail="找不到所选声音库")
    if int(voice.get("enabled_file_count") or 0) < 1:
        raise HTTPException(status_code=422, detail="所选声音库没有启用的录音")
    return voice_id
