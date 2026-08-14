from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile

from ..dependencies import ClientId, ServicesDep
from ..payloads import script_payload
from ..uploads import ScriptStorage


router = APIRouter(prefix="/api/scripts")


@router.get("")
def list_scripts(services: ServicesDep) -> dict[str, Any]:
    return {
        "scripts": [
            script_payload(script) for script in services.database.scripts.list()
        ]
    }


@router.post("", status_code=201)
async def upload_script(
    file: Annotated[UploadFile, File(...)],
    client_id: ClientId,
    services: ServicesDep,
) -> dict[str, Any]:
    script_id, _ = await ScriptStorage(services).store(file, client_id)
    script = services.database.scripts.get(script_id)
    return {"script": script_payload(script) if script else None}
