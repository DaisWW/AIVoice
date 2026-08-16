from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from ..auth import SESSION_COOKIE
from ..services import ApplicationServices


def get_services(request: Request) -> ApplicationServices:
    return request.app.state.services


ServicesDep = Annotated[ApplicationServices, Depends(get_services)]


def current_user(request: Request, services: ServicesDep) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    user = services.auth.user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    request.state.user = user
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def admin_user(user: CurrentUser) -> dict[str, Any]:
    if str(user.get("role")) != "system_admin":
        raise HTTPException(status_code=403, detail="需要系统管理员权限")
    return user


AdminUser = Annotated[dict[str, Any], Depends(admin_user)]


def client_identity(user: CurrentUser) -> str:
    """Compatibility dependency for old route signatures during migration."""
    return str(user["id"])


ClientId = Annotated[str, Depends(client_identity)]
