from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ...auth import AuthError, SESSION_COOKIE, public_user
from ...login_protection import LoginRateLimitError
from ..dependencies import CurrentUser, ServicesDep
from ..schemas import LoginRequest, PasswordChange


router = APIRouter(prefix="/api/auth")


@router.post("/login")
def login(
    changes: LoginRequest, request: Request, response: Response, services: ServicesDep
) -> dict[str, Any]:
    ip_address = _ip(request)
    try:
        user = services.auth.authenticate(
            changes.username,
            changes.password,
            ip_address,
        )
    except LoginRateLimitError as error:
        services.database.audit.record(
            "auth.login_throttled",
            ip_address=ip_address,
            success=False,
            details={"username": changes.username.strip()[:64]},
        )
        raise HTTPException(
            status_code=429,
            detail=str(error),
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    except AuthError as error:
        services.database.audit.record(
            "auth.login_failed",
            ip_address=ip_address,
            success=False,
            details={"username": changes.username.strip()[:64]},
        )
        raise HTTPException(status_code=401, detail=str(error)) from error
    token = services.auth.create_session(
        user,
        ip_address,
        request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=14 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    services.database.audit.record(
        "auth.login",
        actor=user,
        ip_address=ip_address,
    )
    return {"user": public_user(user)}


@router.get("/session")
def session(user: CurrentUser) -> dict[str, Any]:
    return {"user": public_user(user)}


@router.post("/logout")
def logout(
    request: Request, response: Response, user: CurrentUser, services: ServicesDep
) -> dict[str, bool]:
    services.auth.logout(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    services.database.audit.record("auth.logout", actor=user, ip_address=_ip(request))
    return {"ok": True}


@router.post("/change-password")
def change_password(
    changes: PasswordChange,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    try:
        services.auth.change_password(
            user, changes.current_password, changes.new_password
        )
    except AuthError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    services.database.audit.record(
        "auth.password_changed", actor=user, ip_address=_ip(request)
    )
    return {
        "user": public_user(services.database.auth.get_user(str(user["id"])) or user)
    }


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""
