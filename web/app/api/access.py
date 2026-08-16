from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .dependencies import AdminUser, ServicesDep


AdminAccess = AdminUser


def is_system_admin(user: dict[str, Any]) -> bool:
    return str(user.get("role")) == "system_admin"


def resolve_project_id(
    services: ServicesDep,
    user: dict[str, Any],
    requested: str,
) -> str:
    project_id = requested.strip()
    if not project_id:
        project_id = (
            services.database.projects.default_for_user(
                str(user["id"]), include_all=is_system_admin(user)
            )
            or ""
        )
    require_project(services, user, project_id)
    return project_id


def require_project(
    services: ServicesDep,
    user: dict[str, Any],
    project_id: str,
    *,
    manage: bool = False,
) -> dict[str, Any]:
    project = services.database.projects.get(project_id)
    if not project or project["status"] != "active":
        raise HTTPException(status_code=404, detail="找不到项目")
    if is_system_admin(user):
        return project
    role = services.database.projects.role(project_id, str(user["id"]))
    if not role:
        raise HTTPException(status_code=404, detail="找不到项目")
    if manage and role != "owner":
        raise HTTPException(status_code=403, detail="只有项目负责人可以管理成员")
    return project


def project_job(
    services: ServicesDep, job_id: str, user: dict[str, Any]
) -> dict[str, Any]:
    job = services.database.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务")
    require_project(services, user, str(job.get("project_id") or ""))
    return job


def project_voice(
    services: ServicesDep, voice_id: str, user: dict[str, Any]
) -> dict[str, Any]:
    voice = services.database.voices.get(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail="找不到声音库")
    require_project(services, user, str(voice.get("project_id") or ""))
    return voice


def project_script(
    services: ServicesDep, script_id: str, user: dict[str, Any]
) -> dict[str, Any]:
    script = services.database.scripts.get(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="找不到台本")
    require_project(services, user, str(script.get("project_id") or ""))
    return script


def admin_job(services: ServicesDep, job_id: str) -> dict[str, Any]:
    job = services.database.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务")
    return job
