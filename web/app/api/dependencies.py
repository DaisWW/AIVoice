from __future__ import annotations

import re
import secrets
from typing import Annotated

from fastapi import Depends, Request, Response

from ..services import ApplicationServices


CLIENT_ID_RE = re.compile(r"^guest-[0-9a-f]{8,16}$")
CLIENT_COOKIE = "voice_lab_client"


def get_services(request: Request) -> ApplicationServices:
    return request.app.state.services


ServicesDep = Annotated[ApplicationServices, Depends(get_services)]


def client_identity(
    request: Request,
    response: Response,
    services: ServicesDep,
) -> str:
    client_id = request.cookies.get(CLIENT_COOKIE, "")
    if not CLIENT_ID_RE.fullmatch(client_id):
        client_id = f"guest-{secrets.token_hex(8)}"
        response.set_cookie(
            CLIENT_COOKIE,
            client_id,
            max_age=365 * 24 * 60 * 60,
            samesite="lax",
            httponly=True,
        )
    services.database.clients.touch(client_id)
    return client_id


ClientId = Annotated[str, Depends(client_identity)]
