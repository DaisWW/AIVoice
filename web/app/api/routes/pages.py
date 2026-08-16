from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from ..dependencies import ServicesDep


router = APIRouter()


@router.get("/", include_in_schema=False)
def index(services: ServicesDep) -> FileResponse:
    return FileResponse(services.settings.static_root / "index.html")


@router.get("/admin", include_in_schema=False)
def admin_index(services: ServicesDep) -> FileResponse:
    return FileResponse(services.settings.static_root / "admin.html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)
