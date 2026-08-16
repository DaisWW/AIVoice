from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...auth import AuthError, public_user
from ..dependencies import AdminUser, ServicesDep
from ..payloads import JobPresenter
from ..schemas import PasswordReset, UserCreate, UserStatusUpdate


router = APIRouter(prefix="/api/admin")


@router.get("/overview")
def overview(admin: AdminUser, services: ServicesDep) -> dict[str, Any]:
    users = services.database.auth.list_users()
    projects = services.database.projects.list_for_user(
        str(admin["id"]), include_all=True
    )
    jobs = services.database.jobs.list(12)
    usage = shutil.disk_usage(services.settings.data_root)
    return {
        "counts": {
            **services.database.monitoring.counts(),
            "users": len(users),
            "active_users": sum(user["status"] == "active" for user in users),
            "projects": len(projects),
            "memberships": sum(int(project["member_count"]) for project in projects),
        },
        "queue": services.job_queue.status(),
        "engine": services.engine.model_status(),
        "storage": {
            "data_bytes": _directory_size(services.settings.data_root),
            "disk_total_bytes": usage.total,
            "disk_free_bytes": usage.free,
        },
        "recent_jobs": JobPresenter(services).payload_many(jobs),
        "recent_audit": services.database.audit.list(30),
    }


@router.get("/users")
def list_users(_: AdminUser, services: ServicesDep) -> dict[str, Any]:
    return {
        "users": [public_user(user) for user in services.database.auth.list_users()]
    }


@router.post("/users", status_code=201)
def create_user(
    changes: UserCreate,
    request: Request,
    admin: AdminUser,
    services: ServicesDep,
) -> dict[str, Any]:
    try:
        user, _ = services.auth.create_user(
            changes.username, changes.display_name, changes.password
        )
    except AuthError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    services.database.audit.record(
        "admin.user_created",
        actor=admin,
        target_type="user",
        target_id=str(user["id"]),
        ip_address=_ip(request),
        details={"username": user["username"]},
    )
    return {"user": public_user(user)}


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: str,
    changes: UserStatusUpdate,
    request: Request,
    admin: AdminUser,
    services: ServicesDep,
) -> dict[str, Any]:
    if user_id == str(admin["id"]) and changes.status != "active":
        raise HTTPException(status_code=409, detail="不能停用当前管理员账户")
    if not services.database.auth.update_status(user_id, changes.status):
        raise HTTPException(status_code=404, detail="找不到账户")
    user = services.database.auth.get_user(user_id)
    services.database.audit.record(
        "admin.user_status_changed",
        actor=admin,
        target_type="user",
        target_id=user_id,
        ip_address=_ip(request),
        details={"status": changes.status},
    )
    return {"user": public_user(user) if user else None}


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    changes: PasswordReset,
    request: Request,
    admin: AdminUser,
    services: ServicesDep,
) -> dict[str, bool]:
    try:
        services.auth.reset_password(user_id, changes.password)
    except AuthError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    services.database.audit.record(
        "admin.password_reset",
        actor=admin,
        target_type="user",
        target_id=user_id,
        ip_address=_ip(request),
    )
    return {"ok": True}


@router.get("/projects")
def list_projects(admin: AdminUser, services: ServicesDep) -> dict[str, Any]:
    return {
        "projects": services.database.projects.list_for_user(
            str(admin["id"]), include_all=True
        )
    }


@router.get("/audit-logs")
def audit_logs(_: AdminUser, services: ServicesDep, limit: int = 200) -> dict[str, Any]:
    return {"logs": services.database.audit.list(limit)}


def _directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""
