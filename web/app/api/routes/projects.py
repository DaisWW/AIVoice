from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..access import require_project
from ..dependencies import CurrentUser, ServicesDep
from ..schemas import InvitationCreate, ProjectCreate, ProjectUpdate


router = APIRouter(prefix="/api")


@router.get("/projects")
def list_projects(user: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    projects = services.database.projects.list_for_user(str(user["id"]))
    return {
        "projects": [_project_payload(services, project, user) for project in projects],
        "invitations": services.database.projects.invitations_for_user(str(user["id"])),
    }


@router.post("/projects", status_code=201)
def create_project(
    changes: ProjectCreate,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    name, description = _project_fields(changes.name, changes.description)
    project = services.database.projects.create(str(user["id"]), name, description)
    services.database.audit.record(
        "project.created",
        actor=user,
        target_type="project",
        target_id=str(project["id"]),
        project_id=str(project["id"]),
        ip_address=_ip(request),
        details={"name": project["name"]},
    )
    return {"project": _project_payload(services, project, user)}


@router.get("/projects/{project_id}")
def get_project(
    project_id: str, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    project = require_project(services, user, project_id)
    return {"project": _project_payload(services, project, user)}


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    changes: ProjectUpdate,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    project = require_project(services, user, project_id, manage=True)
    name, description = _project_fields(changes.name, changes.description)
    services.database.projects.update(project_id, name, description)
    refreshed = services.database.projects.get(project_id) or project
    services.database.audit.record(
        "project.updated",
        actor=user,
        target_type="project",
        target_id=project_id,
        project_id=project_id,
        ip_address=_ip(request),
    )
    return {"project": _project_payload(services, refreshed, user)}


@router.get("/projects/{project_id}/members")
def list_members(
    project_id: str, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    require_project(services, user, project_id)
    return {"members": services.database.projects.members(project_id)}


@router.post("/projects/{project_id}/invitations", status_code=201)
def invite_member(
    project_id: str,
    changes: InvitationCreate,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    require_project(services, user, project_id, manage=True)
    invited = services.database.auth.get_by_username(changes.username)
    if not invited:
        raise HTTPException(status_code=404, detail="找不到该账户，请先由管理员创建账户")
    if invited["status"] != "active":
        raise HTTPException(status_code=409, detail="该账户已停用，暂时不能加入项目")
    try:
        invitation = services.database.projects.invite(
            project_id, str(invited["id"]), str(user["id"])
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    services.database.audit.record(
        "project.member_invited",
        actor=user,
        target_type="user",
        target_id=str(invited["id"]),
        project_id=project_id,
        ip_address=_ip(request),
        details={"username": invited["username"]},
    )
    return {"invitation": invitation}


@router.post("/invitations/{invitation_id}/accept")
def accept_invitation(
    invitation_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    return _respond_invitation(invitation_id, True, request, user, services)


@router.post("/invitations/{invitation_id}/decline")
def decline_invitation(
    invitation_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    return _respond_invitation(invitation_id, False, request, user, services)


@router.delete("/projects/{project_id}/members/{member_id}")
def remove_member(
    project_id: str,
    member_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, bool]:
    require_project(services, user, project_id, manage=True)
    try:
        removed = services.database.projects.remove_member(project_id, member_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not removed:
        raise HTTPException(status_code=404, detail="该账户不是项目成员")
    services.database.audit.record(
        "project.member_removed",
        actor=user,
        target_type="user",
        target_id=member_id,
        project_id=project_id,
        ip_address=_ip(request),
    )
    return {"ok": True}


def _respond_invitation(
    invitation_id: str,
    accept: bool,
    request: Request,
    user: dict[str, Any],
    services: ServicesDep,
) -> dict[str, Any]:
    try:
        invitation = services.database.projects.respond(
            invitation_id, str(user["id"]), accept
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    services.database.audit.record(
        f"project.invitation_{'accepted' if accept else 'declined'}",
        actor=user,
        target_type="invitation",
        target_id=invitation_id,
        project_id=str(invitation["project_id"]),
        ip_address=_ip(request),
    )
    return {"invitation": invitation}


def _project_payload(
    services: ServicesDep, project: dict[str, Any], user: dict[str, Any]
) -> dict[str, Any]:
    role = services.database.projects.role(str(project["id"]), str(user["id"]))
    return {
        **project,
        "member_role": role,
        "can_manage": role == "owner",
    }


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _project_fields(name: str, description: str) -> tuple[str, str]:
    clean_name = name.strip()
    clean_description = description.strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    return clean_name, clean_description
