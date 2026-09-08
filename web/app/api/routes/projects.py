from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ..access import require_project
from ..dependencies import CurrentUser, ServicesDep
from ..schemas import (
    MemberCreate,
    MemberRoleUpdate,
    ProjectCreate,
    ProjectUpdate,
    PromptSuggestionRequest,
    ScriptImportConfirm,
)
from ..payloads import script_payload
from ..smart_script_import import (
    MAX_SMART_IMPORT_FILES,
    MAX_SMART_IMPORT_UPLOAD_BYTES,
    SmartImportSource,
    SmartScriptImport,
    SmartScriptImportError,
)
from ...script_parser import ScriptFormatError
from ...search import search_text
from ...storage import UPLOAD_CHUNK_SIZE


router = APIRouter(prefix="/api")


@router.get("/projects")
def list_projects(user: CurrentUser, services: ServicesDep) -> dict[str, Any]:
    projects = services.database.projects.list_for_user(str(user["id"]))
    return {
        "projects": [_project_payload(services, project, user) for project in projects]
    }


@router.post("/projects", status_code=201)
def create_project(
    changes: ProjectCreate,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    name, description = _project_fields(changes.name, changes.description)
    prompt = changes.prompt.strip()
    project = services.database.projects.create(
        str(user["id"]), name, description, prompt
    )
    if prompt:
        services.database.context_revisions.record(
            scope="project",
            project_id=str(project["id"]),
            script_id=None,
            content=prompt,
            created_by=str(user["id"]),
        )
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


@router.post("/projects/{project_id}/prompt-suggestion")
def suggest_project_prompt(
    project_id: str,
    changes: PromptSuggestionRequest,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, str]:
    project = require_project(services, user, project_id, manage=True)
    try:
        suggestion = services.text_generation.suggest_prompt(
            scope="project",
            project_prompt=str(project.get("prompt") or ""),
            script_prompt="",
            goal=changes.goal,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    services.database.audit.record(
        "project.prompt_suggested",
        actor=user,
        target_type="project",
        target_id=project_id,
        project_id=project_id,
        ip_address=request.client.host if request.client else "",
    )
    return {"suggestion": suggestion}


@router.get("/projects/{project_id}/script-imports/pending")
def pending_script_import(
    project_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    require_project(services, user, project_id)
    try:
        batch = SmartScriptImport(services).pending(project_id, str(user["id"]))
    except SmartScriptImportError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"batch": batch}


@router.post("/projects/{project_id}/script-imports/analyze")
async def analyze_script_import(
    project_id: str,
    files: Annotated[list[UploadFile], File(...)],
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    project = require_project(services, user, project_id)
    try:
        if len(files) > MAX_SMART_IMPORT_FILES:
            raise SmartScriptImportError(f"一次最多导入 {MAX_SMART_IMPORT_FILES} 个文件")
        remaining = MAX_SMART_IMPORT_UPLOAD_BYTES
        sources: list[SmartImportSource] = []
        for upload in files:
            content = await _read_smart_import_upload(upload, remaining)
            remaining -= len(content)
            sources.append(
                SmartImportSource(filename=upload.filename or "台本.txt", content=content)
            )
        batch = await run_in_threadpool(
            SmartScriptImport(services).analyze,
            project,
            str(user["id"]),
            sources,
        )
    except SmartScriptImportError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    services.database.audit.record(
        "script_import.analyzed",
        actor=user,
        target_type="script_import",
        target_id=str(batch["batch_id"]),
        project_id=project_id,
        ip_address=_ip(request),
        details={
            "source_file_count": len(files),
            "draft_count": len(batch["drafts"]),
            "dialogue_line_count": batch["dialogue_line_count"],
        },
    )
    return {"batch": batch}


@router.post("/projects/{project_id}/script-imports/confirm", status_code=201)
def confirm_script_import(
    project_id: str,
    changes: ScriptImportConfirm,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    require_project(services, user, project_id)
    try:
        scripts = SmartScriptImport(services).confirm(
            project_id,
            str(user["id"]),
            changes.batch_id,
            [draft.model_dump() for draft in changes.drafts],
        )
    except (ScriptFormatError, SmartScriptImportError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    services.database.audit.record(
        "script_import.confirmed",
        actor=user,
        target_type="script_import",
        target_id=changes.batch_id,
        project_id=project_id,
        ip_address=_ip(request),
        details={"script_count": len(scripts)},
    )
    return {"scripts": [script_payload(script) for script in scripts]}


@router.delete("/projects/{project_id}/script-imports/pending", status_code=204)
def discard_corrupt_script_import(
    project_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> None:
    require_project(services, user, project_id)
    try:
        SmartScriptImport(services).discard_corrupt(project_id, str(user["id"]))
    except SmartScriptImportError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    services.database.audit.record(
        "script_import.corrupt_discarded",
        actor=user,
        target_type="script_import",
        target_id="pending",
        project_id=project_id,
        ip_address=_ip(request),
    )


@router.delete("/projects/{project_id}/script-imports/{batch_id}", status_code=204)
def discard_script_import(
    project_id: str,
    batch_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> None:
    require_project(services, user, project_id)
    try:
        SmartScriptImport(services).discard(project_id, str(user["id"]), batch_id)
    except SmartScriptImportError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    services.database.audit.record(
        "script_import.discarded",
        actor=user,
        target_type="script_import",
        target_id=batch_id,
        project_id=project_id,
        ip_address=_ip(request),
    )


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
    prompt = (
        str(changes.prompt).strip()
        if changes.prompt is not None
        else str(project.get("prompt") or "")
    )
    services.database.projects.update(project_id, name, description, prompt)
    refreshed = services.database.projects.get(project_id) or project
    services.database.context_revisions.record(
        scope="project",
        project_id=project_id,
        script_id=None,
        content=prompt,
        created_by=str(user["id"]),
    )
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


@router.get("/projects/{project_id}/member-candidates")
def member_candidates(
    project_id: str,
    user: CurrentUser,
    services: ServicesDep,
    q: str = Query(default="", max_length=64),
) -> dict[str, Any]:
    require_project(services, user, project_id, manage=True)
    return {"candidates": services.database.auth.member_candidates(project_id, q)}


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(
    project_id: str,
    changes: MemberCreate,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, bool]:
    require_project(services, user, project_id, manage=True)
    member = services.database.auth.get_by_username(changes.username)
    if not member:
        raise HTTPException(status_code=404, detail="找不到该账户，请先由管理员创建账户")
    if member["status"] != "active":
        raise HTTPException(status_code=409, detail="该账户已停用，暂时不能加入项目")
    try:
        services.database.projects.add_member(
            project_id, str(member["id"]), str(user["id"])
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    services.database.audit.record(
        "project.member_added",
        actor=user,
        target_type="user",
        target_id=str(member["id"]),
        project_id=project_id,
        ip_address=_ip(request),
        details={"username": member["username"]},
    )
    return {"ok": True}


@router.delete("/projects/{project_id}/members/{member_id}")
def remove_member(
    project_id: str,
    member_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, bool]:
    is_self = member_id == str(user["id"])
    project = require_project(services, user, project_id, manage=not is_self)
    if is_self and str(project["owner_id"]) == member_id:
        raise HTTPException(status_code=409, detail="项目负责人不能退出项目")
    try:
        removed = services.database.projects.remove_member(project_id, member_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not removed:
        raise HTTPException(status_code=404, detail="该账户不是项目成员")
    services.database.audit.record(
        "project.member_left" if is_self else "project.member_removed",
        actor=user,
        target_type="user",
        target_id=member_id,
        project_id=project_id,
        ip_address=_ip(request),
    )
    return {"ok": True}


@router.patch("/projects/{project_id}/members/{member_id}")
def update_member_role(
    project_id: str,
    member_id: str,
    changes: MemberRoleUpdate,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    project = require_project(services, user, project_id, manage=True)
    if str(project["owner_id"]) == member_id:
        raise HTTPException(status_code=409, detail="项目所有者的权限不能修改")
    try:
        updated = services.database.projects.update_member_role(
            project_id, member_id, changes.role
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail="该账户不是项目成员")
    services.database.audit.record(
        "project.member_role_updated",
        actor=user,
        target_type="user",
        target_id=member_id,
        project_id=project_id,
        ip_address=_ip(request),
        details={"role": changes.role},
    )
    member = next(
        (
            item
            for item in services.database.projects.members(project_id)
            if str(item["id"]) == member_id
        ),
        None,
    )
    return {"member": member} if member else {"member": None}


def _project_payload(
    services: ServicesDep, project: dict[str, Any], user: dict[str, Any]
) -> dict[str, Any]:
    role = services.database.projects.role(str(project["id"]), str(user["id"]))
    return {
        **project,
        "project_role": role,
        "can_manage": role in {"owner", "admin"},
        "search_text": search_text(
            project.get("name"),
            project.get("description"),
            project.get("owner_name"),
            project.get("owner_username"),
        ),
    }


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _project_fields(name: str, description: str) -> tuple[str, str]:
    clean_name = name.strip()
    clean_description = description.strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    return clean_name, clean_description


async def _read_smart_import_upload(upload: UploadFile, remaining: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(
        max(1, min(UPLOAD_CHUNK_SIZE, remaining - total + 1))
    ):
        total += len(chunk)
        if total > remaining:
            raise SmartScriptImportError("本次上传文件总大小不能超过 20 MB")
        chunks.append(chunk)
    return b"".join(chunks)
