from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile

from ..access import resolve_project_id
from ..audit import record_action
from ..dependencies import CurrentUser, ServicesDep
from ..payloads import script_payload
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
    script_id, _ = await ScriptStorage(services).store(file, str(user["id"]), selected)
    script = services.database.scripts.get(script_id)
    record_action(
        services,
        request,
        user,
        "script.uploaded",
        target_type="script",
        target_id=script_id,
        project_id=selected,
        details={"name": script["name"] if script else ""},
    )
    return {"script": script_payload(script) if script else None}
