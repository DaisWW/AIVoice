from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from ..access import AdminAccess, set_admin_cookie_if_bootstrapped
from ..dependencies import ServicesDep


router = APIRouter()


@router.get("/", include_in_schema=False)
def index(services: ServicesDep) -> FileResponse:
    return FileResponse(services.settings.static_root / "index.html")


@router.get("/admin", include_in_schema=False, response_model=None)
def admin_index(
    request: Request, services: ServicesDep, _: AdminAccess
) -> FileResponse | RedirectResponse:
    redirect = RedirectResponse("/admin", status_code=303)
    if set_admin_cookie_if_bootstrapped(request, redirect):
        return redirect
    return FileResponse(services.settings.static_root / "index.html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)
