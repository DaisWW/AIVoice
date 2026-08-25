from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ...domain import ScriptItem
from ...script_parser import ScriptFormatError, build_script_item
from ...storage import ensure_within, safe_filename
from ..access import project_script, resolve_project_id
from ..audit import record_action
from ..dependencies import CurrentUser, ServicesDep
from ..payloads import script_detail_payload, script_payload
from ..schemas import ScriptItemsUpdate, ScriptUpdate
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
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    script_id, _ = await ScriptStorage(services).store(
        file,
        str(user["id"]),
        selected,
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
        details={"name": script["name"]},
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
    name = changes.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="台本名称不能为空")
    if not services.database.scripts.update(script_id, name):
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
        details={"name": name},
    )
    return {"script": script_payload(updated)}


@router.put("/{script_id}/items")
def update_script_items(
    script_id: str,
    changes: ScriptItemsUpdate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    items = _build_items(changes)
    ScriptStorage(services).save_items(script, items)
    updated = services.database.scripts.get(script_id)
    if not updated:  # pragma: no cover - guarded by update_source
        raise HTTPException(status_code=500, detail="台本保存后未找到")
    record_action(
        services,
        request,
        user,
        "script.items_updated",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"item_count": len(items)},
    )
    return {"script": script_detail_payload(updated, items)}


@router.post("/{script_id}/import")
async def import_script(
    script_id: str,
    file: Annotated[UploadFile, File(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    items = await ScriptStorage(services).replace_from_upload(script, file)
    updated = services.database.scripts.get(script_id)
    if not updated:  # pragma: no cover - guarded by update_source
        raise HTTPException(status_code=500, detail="台本导入后未找到")
    record_action(
        services,
        request,
        user,
        "script.imported",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"item_count": len(items), "filename": file.filename or ""},
    )
    return {"script": script_detail_payload(updated, items)}


@router.get("/{script_id}/export")
def export_script(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> Response:
    script = project_script(services, script_id, user)
    items = ScriptStorage(services).load_items(script)
    filename = safe_filename(f"{script['name']}.csv")
    return Response(
        content=ScriptStorage.export_csv(items),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"script.csv\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.delete("/{script_id}", status_code=204)
def delete_script(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> Response:
    script = project_script(services, script_id, user)
    if services.database.scripts.has_jobs(script_id):
        raise HTTPException(status_code=409, detail="请先删除引用该台本的生成记录")
    source_path = ensure_within(
        Path(str(script["source_path"])), services.settings.root
    )
    if not services.database.scripts.delete(script_id):
        raise HTTPException(status_code=404, detail="找不到台本")
    source_path.unlink(missing_ok=True)
    record_action(
        services,
        request,
        user,
        "script.deleted",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"name": script["name"]},
    )
    return Response(status_code=204)


def _build_items(changes: ScriptItemsUpdate) -> list[ScriptItem]:
    items: list[ScriptItem] = []
    for order, row in enumerate(changes.items, start=1):
        try:
            items.append(
                build_script_item(
                    row.text,
                    row.pronunciation,
                    order,
                    order,
                )
            )
        except ScriptFormatError as error:
            raise HTTPException(
                status_code=422, detail=f"第 {order} 行发音格式错误: {error}"
            ) from error
    return items
