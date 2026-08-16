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
    insights = services.database.monitoring.admin_insights()
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
        "insights": insights,
    }


@router.get("/users")
def list_users(_: AdminUser, services: ServicesDep) -> dict[str, Any]:
    return {
        "users": [
            _admin_user_payload(user) for user in services.database.auth.list_users()
        ]
    }


@router.get("/assets")
def list_assets(
    _: AdminUser,
    services: ServicesDep,
    project_id: str = "",
    limit: int = 300,
) -> dict[str, Any]:
    selected = project_id.strip()
    bounded_limit = min(max(limit, 1), 500)
    users = {str(user["id"]): user for user in services.database.auth.list_users()}
    projects = {
        str(project["id"]): project
        for project in services.database.projects.list_for_user("", include_all=True)
    }

    def project_name(value: str) -> str:
        return str(projects.get(value, {}).get("name") or value or "未分配项目")

    def owner_name(value: str) -> str:
        owner = users.get(value, {})
        return str(owner.get("display_name") or owner.get("username") or value)

    voices = services.database.voices.list(selected or None)[:bounded_limit]
    scripts = services.database.scripts.list(selected or None)[:bounded_limit]
    return {
        "voices": [
            {
                "id": str(voice["id"]),
                "name": str(voice["name"]),
                "project_id": str(voice.get("project_id") or ""),
                "project_name": project_name(str(voice.get("project_id") or "")),
                "owner_id": str(voice.get("owner_id") or ""),
                "owner_name": owner_name(str(voice.get("owner_id") or "")),
                "file_count": int(voice.get("file_count") or 0),
                "enabled_file_count": int(voice.get("enabled_file_count") or 0),
                "size_bytes": int(voice.get("size_bytes") or 0),
                "source_kind": str(voice.get("source_kind") or ""),
                "created_at": voice.get("created_at"),
            }
            for voice in voices
        ],
        "scripts": [
            {
                "id": str(script["id"]),
                "name": str(script["name"]),
                "original_name": str(script.get("original_name") or ""),
                "project_id": str(script.get("project_id") or ""),
                "project_name": project_name(str(script.get("project_id") or "")),
                "owner_id": str(script.get("owner_id") or ""),
                "owner_name": owner_name(str(script.get("owner_id") or "")),
                "item_count": int(script.get("item_count") or 0),
                "source_kind": str(script.get("source_kind") or ""),
                "created_at": script.get("created_at"),
            }
            for script in scripts
        ],
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


@router.get("/projects/{project_id}")
def project_detail(
    project_id: str, _: AdminUser, services: ServicesDep
) -> dict[str, Any]:
    project = services.database.projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="找不到项目")
    presenter = JobPresenter(services)
    jobs = services.database.jobs.list_for_project(project_id, 20)
    return {
        "project": project,
        "members": services.database.projects.members(project_id),
        "voices": [
            _voice_summary(voice) for voice in services.database.voices.list(project_id)
        ],
        "scripts": [
            _script_summary(script)
            for script in services.database.scripts.list(project_id)
        ],
        "recent_jobs": presenter.payload_many(jobs, api_prefix="/api/admin/jobs"),
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


def _admin_user_payload(user: dict[str, Any]) -> dict[str, Any]:
    payload = public_user(user)
    payload.update(
        {
            "job_count": int(user.get("job_count") or 0),
            "voice_count": int(user.get("voice_count") or 0),
            "script_count": int(user.get("script_count") or 0),
            "last_activity_at": user.get("last_activity_at"),
        }
    )
    return payload


def _voice_summary(voice: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(voice["id"]),
        "name": str(voice["name"]),
        "file_count": int(voice.get("file_count") or 0),
        "enabled_file_count": int(voice.get("enabled_file_count") or 0),
        "size_bytes": int(voice.get("size_bytes") or 0),
        "created_at": voice.get("created_at"),
    }


def _script_summary(script: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(script["id"]),
        "name": str(script["name"]),
        "item_count": int(script.get("item_count") or 0),
        "source_kind": str(script.get("source_kind") or ""),
        "created_at": script.get("created_at"),
    }


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""
