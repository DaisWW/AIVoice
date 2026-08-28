from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...auth import AuthError, public_user
from ...value_utils import stored_int
from ..dependencies import AdminUser, ServicesDep
from ..payloads import JobPresenter
from ..schemas import PasswordReset, TextModelUpdate, UserCreate, UserStatusUpdate


router = APIRouter(prefix="/api/admin")


@router.get("/text-model")
def get_text_model(_: AdminUser, services: ServicesDep) -> dict[str, Any]:
    try:
        payload = services.text_generation.admin()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"text_model": payload}


@router.patch("/text-model")
def update_text_model(
    changes: TextModelUpdate,
    request: Request,
    admin: AdminUser,
    services: ServicesDep,
) -> dict[str, Any]:
    try:
        payload = services.text_generation.update(changes.model_dump())
    except (RuntimeError, TypeError, ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    services.database.audit.record(
        "admin.text_model_updated",
        actor=admin,
        target_type="text_model",
        target_id="default",
        ip_address=_ip(request),
        details={"enabled": changes.enabled, "model": changes.model},
    )
    return {"text_model": payload}


_STORAGE_CACHE: dict[str, tuple[float, int]] = {}
_STORAGE_CACHE_LOCK = Lock()
_STORAGE_CACHE_TTL = 15.0
_STORAGE_CACHE_MAX_ENTRIES = 8


@router.get("/overview")
def overview(_: AdminUser, services: ServicesDep) -> dict[str, Any]:
    user_counts = services.database.auth.overview_counts()
    project_counts = services.database.projects.overview_counts()
    jobs = services.database.jobs.list(12)
    usage = shutil.disk_usage(services.settings.data_root)
    insights = services.database.monitoring.admin_insights()
    return {
        "counts": {
            **services.database.monitoring.counts(),
            **user_counts,
            **project_counts,
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
    selected = _project_filter(project_id)
    bounded_limit = _bounded_limit(limit, 500)
    users = {str(user["id"]): user for user in services.database.auth.list_users()}
    projects = {
        str(project["id"]): project
        for project in services.database.projects.list_for_user("", include_all=True)
    }
    voices = services.database.voices.list(selected, limit=bounded_limit)
    scripts = services.database.scripts.list(selected, limit=bounded_limit)
    return {
        "voices": [_voice_asset(voice, projects, users) for voice in voices],
        "scripts": [_script_asset(script, projects, users) for script in scripts],
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
    return {"logs": services.database.audit.list(_bounded_limit(limit, 1000))}


def _directory_size(root: Path) -> int:
    path = Path(root)
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    # A single disk walk serves concurrent overview requests and keeps the TTL useful.
    with _STORAGE_CACHE_LOCK:
        now = time.monotonic()
        cached = _STORAGE_CACHE.get(key)
        if cached and now - cached[0] < _STORAGE_CACHE_TTL:
            return cached[1]
        total = _scan_directory(path)
        _STORAGE_CACHE[key] = (time.monotonic(), total)
        _trim_storage_cache()
        return total


def _scan_directory(root: Path) -> int:
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def _trim_storage_cache() -> None:
    while len(_STORAGE_CACHE) > _STORAGE_CACHE_MAX_ENTRIES:
        oldest = min(_STORAGE_CACHE, key=lambda key: _STORAGE_CACHE[key][0])
        del _STORAGE_CACHE[oldest]


def _bounded_limit(value: int, maximum: int) -> int:
    return min(max(stored_int(value, 1), 1), maximum)


def _project_filter(value: str) -> str | None:
    selected = value.strip()
    return selected if selected and selected.lower() != "all" else None


def _project_name(projects: dict[str, dict[str, Any]], project_id: str) -> str:
    return str(projects.get(project_id, {}).get("name") or project_id or "未分配项目")


def _owner_name(users: dict[str, dict[str, Any]], owner_id: str) -> str:
    owner = users.get(owner_id, {})
    return str(owner.get("display_name") or owner.get("username") or owner_id)


def _voice_asset(
    voice: dict[str, Any],
    projects: dict[str, dict[str, Any]],
    users: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    project_id = str(voice.get("project_id") or "")
    owner_id = str(voice.get("owner_id") or "")
    return {
        "id": str(voice["id"]),
        "name": str(voice["name"]),
        "project_id": project_id,
        "project_name": _project_name(projects, project_id),
        "owner_id": owner_id,
        "owner_name": _owner_name(users, owner_id),
        "file_count": stored_int(voice.get("file_count")),
        "enabled_file_count": stored_int(voice.get("enabled_file_count")),
        "size_bytes": stored_int(voice.get("size_bytes")),
        "source_kind": str(voice.get("source_kind") or ""),
        "created_at": voice.get("created_at"),
    }


def _script_asset(
    script: dict[str, Any],
    projects: dict[str, dict[str, Any]],
    users: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    project_id = str(script.get("project_id") or "")
    owner_id = str(script.get("owner_id") or "")
    return {
        "id": str(script["id"]),
        "name": str(script["name"]),
        "original_name": str(script.get("original_name") or ""),
        "project_id": project_id,
        "project_name": _project_name(projects, project_id),
        "owner_id": owner_id,
        "owner_name": _owner_name(users, owner_id),
        "item_count": stored_int(script.get("item_count")),
        "source_kind": str(script.get("source_kind") or ""),
        "created_at": script.get("created_at"),
    }


def _admin_user_payload(user: dict[str, Any]) -> dict[str, Any]:
    payload = public_user(user)
    payload.update(
        {
            "job_count": stored_int(user.get("job_count")),
            "voice_count": stored_int(user.get("voice_count")),
            "script_count": stored_int(user.get("script_count")),
            "last_activity_at": user.get("last_activity_at"),
        }
    )
    return payload


def _voice_summary(voice: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(voice["id"]),
        "name": str(voice["name"]),
        "file_count": stored_int(voice.get("file_count")),
        "enabled_file_count": stored_int(voice.get("enabled_file_count")),
        "size_bytes": stored_int(voice.get("size_bytes")),
        "created_at": voice.get("created_at"),
    }


def _script_summary(script: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(script["id"]),
        "name": str(script["name"]),
        "item_count": stored_int(script.get("item_count")),
        "source_kind": str(script.get("source_kind") or ""),
        "created_at": script.get("created_at"),
    }


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""
