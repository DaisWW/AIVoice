from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ...domain import ScriptItem
from ...script_parser import ScriptFormatError, build_script_item
from ...storage import ensure_within, safe_filename
from ..access import project_script, resolve_project_id
from ..audit import record_action
from ..cleanup import remove_script_exports
from ..dependencies import CurrentUser, ServicesDep
from ..downloads import JobDownloadService
from ..payloads import script_detail_payload, script_payload
from ..schemas import (
    PromptSuggestionRequest,
    ScriptLineSelectionCreate,
    ScriptItemsUpdate,
    ScriptPronunciationGenerationRequest,
    ScriptUpdate,
    TextGenerationRequest,
    TextLineRewriteRequest,
)
from ..uploads import ScriptStorage


router = APIRouter(prefix="/api/scripts")


@router.get("")
def list_scripts(
    user: CurrentUser, services: ServicesDep, project_id: str = ""
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    return {
        "scripts": [
            script_payload(script)
            for script in services.database.scripts.list(selected)
        ]
    }


@router.post("", status_code=201)
async def upload_script(
    file: Annotated[UploadFile, File(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    selected = resolve_project_id(services, user, project_id)
    script_id, _ = await ScriptStorage(services).store(
        file,
        str(user["id"]),
        selected,
    )
    script = services.database.scripts.get(script_id)
    if not script:  # pragma: no cover - guarded by the insert above
        raise HTTPException(status_code=500, detail="台本上传后未找到")
    record_action(
        services,
        request,
        user,
        "script.uploaded",
        target_type="script",
        target_id=script_id,
        project_id=selected,
        details={"name": script["name"]},
    )
    return {"script": script_payload(script)}


@router.get("/{script_id}")
def get_script(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    items = ScriptStorage(services).load_items(script)
    payload = script_detail_payload(script, items)
    payload["selections"] = services.database.selections.list_for_script(script_id)
    return {"script": payload}


@router.get("/{script_id}/selections")
def list_script_selections(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    project_script(services, script_id, user)
    return {"selections": services.database.selections.list_for_script(script_id)}


@router.post("/{script_id}/selections")
def select_script_candidate(
    script_id: str,
    changes: ScriptLineSelectionCreate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    with services.job_mutation_lock:
        job = project_job_for_script(services, changes.job_id, script_id, user)
        item = services.database.jobs.item(changes.job_id, changes.item_id)
        try:
            sequence = int(item.get("sequence")) if item else 0
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(status_code=409, detail="任务记录损坏，无法采纳") from None
        if not item or sequence != changes.sequence:
            raise HTTPException(status_code=404, detail="找不到对应台词行")
        candidate, _ = JobDownloadService(services).candidate_path(
            job, changes.item_id, changes.candidate_id
        )
        if candidate["status"] != "completed":
            raise HTTPException(status_code=409, detail="候选音频完成后才能采纳")
        if not services.database.candidates.accept(
            changes.item_id, changes.candidate_id
        ):
            raise HTTPException(status_code=409, detail="候选音频完成后才能采纳")
        selection = services.database.selections.upsert(
            script_id,
            changes.sequence,
            changes.job_id,
            changes.item_id,
            changes.candidate_id,
            str(user["id"]),
        )
        remove_script_exports(services, script_id)
    record_action(
        services,
        request,
        user,
        "script.line_selected",
        target_type="candidate",
        target_id=changes.candidate_id,
        project_id=str(script["project_id"]),
        details={"script_id": script_id, "sequence": changes.sequence},
    )
    return {"selection": selection}


@router.delete("/{script_id}/selections/{sequence}", status_code=204)
def clear_script_selection(
    script_id: str,
    sequence: int,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> Response:
    with services.job_mutation_lock:
        script = project_script(services, script_id, user)
        if not services.database.selections.delete(script_id, sequence):
            raise HTTPException(status_code=404, detail="该台词行尚未采纳音频")
        remove_script_exports(services, script_id)
    record_action(
        services,
        request,
        user,
        "script.line_selection_cleared",
        target_type="script_line",
        target_id=f"{script_id}:{sequence}",
        project_id=str(script["project_id"]),
        details={"script_id": script_id, "sequence": sequence},
    )
    return Response(status_code=204)


@router.patch("/{script_id}")
def update_script(
    script_id: str,
    changes: ScriptUpdate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    name = changes.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="台本名称不能为空")
    with services.job_mutation_lock:
        script = project_script(services, script_id, user)
        prompt = (
            changes.prompt
            if changes.prompt is not None
            else str(script.get("prompt") or "")
        )
        if not services.database.scripts.update(
            script_id, name, prompt, expected_version=changes.version
        ):
            if changes.version is not None and services.database.scripts.get(script_id):
                raise HTTPException(status_code=409, detail="台本已被其他用户修改，请刷新后重试")
            raise HTTPException(status_code=404, detail="找不到台本")
        remove_script_exports(services, script_id)
        updated = services.database.scripts.get(script_id)
        if not updated:  # pragma: no cover - guarded by the update above
            raise HTTPException(status_code=500, detail="台本更新后未找到")
    record_action(
        services,
        request,
        user,
        "script.updated",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"name": name},
    )
    return {"script": script_payload(updated)}


@router.post("/{script_id}/prompt-suggestion")
def suggest_script_prompt(
    script_id: str,
    changes: PromptSuggestionRequest,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    project = services.database.projects.get(str(script["project_id"])) or {}
    try:
        suggestion = services.text_generation.suggest_prompt(
            scope="script",
            project_prompt=str(project.get("prompt") or ""),
            script_prompt=str(script.get("prompt") or ""),
            goal=changes.goal,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    services.database.audit.record(
        "script.prompt_suggested",
        actor=user,
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        ip_address=request.client.host if request.client else "",
    )
    return {"suggestion": suggestion}


@router.post("/{script_id}/generate-text")
def generate_script_text(
    script_id: str,
    changes: TextGenerationRequest,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    project = services.database.projects.get(str(script["project_id"])) or {}
    try:
        lines = services.text_generation.generate_lines(
            project_prompt=str(project.get("prompt") or ""),
            script_prompt=str(script.get("prompt") or ""),
            instruction=changes.instruction,
            line_count=changes.line_count,
            model_id=changes.model_id,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    services.database.audit.record(
        "script.text_generated",
        actor=user,
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        ip_address=request.client.host if request.client else "",
        details={"line_count": len(lines)},
    )
    return {"lines": lines}


@router.post("/{script_id}/rewrite-line")
def rewrite_script_line(
    script_id: str,
    changes: TextLineRewriteRequest,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    text = changes.text.strip()
    instruction = changes.instruction.strip()
    if not text:
        raise HTTPException(status_code=422, detail="当前台词不能为空")
    if not instruction:
        raise HTTPException(status_code=422, detail="请输入单行修改要求")
    storage = ScriptStorage(services)
    with services.job_mutation_lock:
        script = project_script(services, script_id, user)
        old_items = storage.load_items(script)
        if changes.sequence > len(old_items):
            raise HTTPException(status_code=404, detail="找不到对应台词行")
        items = list(old_items)
        current = old_items[changes.sequence - 1]
        try:
            items[changes.sequence - 1] = build_script_item(
                text,
                changes.pronunciation,
                current.source_line,
                current.order,
                instruction,
            )
        except ScriptFormatError as error:
            raise HTTPException(
                status_code=422, detail=f"第 {changes.sequence} 行发音格式错误: {error}"
            ) from error
        changed = _items_changed(old_items, items)
        persisted_changed = _items_persisted_changed(old_items, items)
        if changes.version is not None:
            try:
                script_version = int(script.get("version") or 1)
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(status_code=409, detail="台本记录损坏，无法保存") from None
            if script_version != changes.version:
                raise HTTPException(status_code=409, detail="台本已被其他用户修改，请刷新后重试")
        if persisted_changed:
            storage.save_items(script, items, expected_version=changes.version)
            if changed:
                services.database.selections.clear_for_script(script_id)
            remove_script_exports(services, script_id)
        updated = services.database.scripts.get(script_id)
        if not updated:  # pragma: no cover - guarded by update_source
            raise HTTPException(status_code=500, detail="台本保存后未找到")
    project = services.database.projects.get(str(updated["project_id"])) or {}
    try:
        line = services.text_generation.rewrite_line(
            project_prompt=str(project.get("prompt") or ""),
            script_prompt=str(updated.get("prompt") or ""),
            text=text,
            pronunciation=changes.pronunciation,
            instruction=instruction,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    services.database.audit.record(
        "script.line_text_suggested",
        actor=user,
        target_type="script_line",
        target_id=f"{script_id}:{changes.sequence}",
        project_id=str(script["project_id"]),
        ip_address=request.client.host if request.client else "",
        details={"sequence": changes.sequence},
    )
    payload = script_detail_payload(updated, items)
    payload["selections"] = (
        [] if changed else services.database.selections.list_for_script(script_id)
    )
    return {"line": line, "script": payload}


@router.post("/{script_id}/generate-pronunciations")
def generate_script_pronunciations(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    changes: ScriptPronunciationGenerationRequest | None = None,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    try:
        version = int(script.get("version") or 1)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=409, detail="台本记录损坏，无法生成发音") from None
    requested_version = changes.version if changes else None
    if requested_version is not None and version != requested_version:
        raise HTTPException(status_code=409, detail="台本已被其他用户修改，请刷新后重试")
    items = ScriptStorage(services).load_items(script)
    if not items:
        raise HTTPException(status_code=422, detail="台本没有可生成的台词")
    project = services.database.projects.get(str(script["project_id"])) or {}
    model_items = [
        {
            "sequence": item.order,
            "text": item.text,
            "pronunciation": item.pronunciation,
            "rewrite_instruction": item.rewrite_instruction,
        }
        for item in items
    ]
    try:
        lines = services.text_generation.generate_pronunciations(
            project_prompt=str(project.get("prompt") or ""),
            script_prompt=str(script.get("prompt") or ""),
            items=model_items,
            model_id=changes.model_id if changes else "",
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    services.database.audit.record(
        "script.pronunciations_generated",
        actor=user,
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        ip_address=request.client.host if request.client else "",
        details={"line_count": len(lines)},
    )
    return {"lines": lines, "version": version}


@router.put("/{script_id}/items")
def update_script_items(
    script_id: str,
    changes: ScriptItemsUpdate,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> dict[str, Any]:
    items = _build_items(changes)
    storage = ScriptStorage(services)
    with services.job_mutation_lock:
        script = project_script(services, script_id, user)
        old_items = storage.load_items(script)
        changed = _items_changed(old_items, items)
        storage.save_items(script, items, expected_version=changes.version)
        if changed:
            services.database.selections.clear_for_script(script_id)
        remove_script_exports(services, script_id)
        updated = services.database.scripts.get(script_id)
        if not updated:  # pragma: no cover - guarded by update_source
            raise HTTPException(status_code=500, detail="台本保存后未找到")
    record_action(
        services,
        request,
        user,
        "script.items_updated",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"item_count": len(items)},
    )
    payload = script_detail_payload(updated, items)
    payload["selections"] = (
        [] if changed else services.database.selections.list_for_script(script_id)
    )
    return {"script": payload}


@router.post("/{script_id}/import")
async def import_script(
    script_id: str,
    file: Annotated[UploadFile, File(...)],
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
    version: int | None = None,
) -> dict[str, Any]:
    storage = ScriptStorage(services)
    items, original_name = await storage.prepare_upload(file)
    with services.job_mutation_lock:
        script = project_script(services, script_id, user)
        try:
            script_version = int(script.get("version") or 1)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(status_code=409, detail="台本记录损坏，无法导入") from None
        if version is not None and script_version != version:
            raise HTTPException(status_code=409, detail="台本已被其他用户修改，请刷新后重试")
        old_items = storage.load_items(script)
        changed = _items_changed(old_items, items)
        storage.save_items(
            script,
            items,
            original_name=original_name,
            expected_version=version,
        )
        if changed:
            services.database.selections.clear_for_script(script_id)
        remove_script_exports(services, script_id)
        updated = services.database.scripts.get(script_id)
        if not updated:  # pragma: no cover - guarded by update_source
            raise HTTPException(status_code=500, detail="台本导入后未找到")
    record_action(
        services,
        request,
        user,
        "script.imported",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"item_count": len(items), "filename": file.filename or ""},
    )
    payload = script_detail_payload(updated, items)
    payload["selections"] = (
        [] if changed else services.database.selections.list_for_script(script_id)
    )
    return {"script": payload}


@router.get("/{script_id}/export")
def export_script(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
) -> Response:
    script = project_script(services, script_id, user)
    items = ScriptStorage(services).load_items(script)
    filename = safe_filename(f"{script['name']}.csv")
    return Response(
        content=ScriptStorage.export_csv(items),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"script.csv\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{script_id}/audio-export")
def export_script_audio(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
    scope: str = "accepted",
) -> Response:
    script = project_script(services, script_id, user)
    return JobDownloadService(services).script_archive_response(script, scope)


@router.delete("/{script_id}", status_code=204)
def delete_script(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
    request: Request,
) -> Response:
    with services.job_mutation_lock:
        script = project_script(services, script_id, user)
        if services.database.scripts.has_jobs(script_id):
            raise HTTPException(status_code=409, detail="请先删除引用该台本的生成记录")
        try:
            source_path = ensure_within(
                Path(str(script["source_path"])), services.settings.root
            )
        except ValueError:
            raise HTTPException(status_code=409, detail="台本源文件路径无效") from None
        if not services.database.scripts.delete(script_id):
            raise HTTPException(status_code=404, detail="找不到台本")
        try:
            source_path.unlink(missing_ok=True)
        except OSError:
            # The database row is already gone; an inaccessible source file
            # should not turn a successful delete into a 500 response.
            pass
        remove_script_exports(services, script_id)
    record_action(
        services,
        request,
        user,
        "script.deleted",
        target_type="script",
        target_id=script_id,
        project_id=str(script["project_id"]),
        details={"name": script["name"]},
    )
    return Response(status_code=204)


def _build_items(changes: ScriptItemsUpdate) -> list[ScriptItem]:
    items: list[ScriptItem] = []
    for order, row in enumerate(changes.items, start=1):
        try:
            items.append(
                build_script_item(
                    row.text,
                    row.pronunciation,
                    order,
                    order,
                    row.rewrite_instruction,
                )
            )
        except ScriptFormatError as error:
            raise HTTPException(
                status_code=422, detail=f"第 {order} 行发音格式错误: {error}"
            ) from error
    return items


def _items_changed(before: list[ScriptItem], after: list[ScriptItem]) -> bool:
    return [(item.text, item.pronunciation) for item in before] != [
        (item.text, item.pronunciation) for item in after
    ]


def _items_persisted_changed(before: list[ScriptItem], after: list[ScriptItem]) -> bool:
    return [
        (item.text, item.pronunciation, item.rewrite_instruction) for item in before
    ] != [(item.text, item.pronunciation, item.rewrite_instruction) for item in after]


def project_job_for_script(
    services: ServicesDep,
    job_id: str,
    script_id: str,
    user: dict[str, Any],
) -> dict[str, Any]:
    job = services.database.jobs.get(job_id)
    if not job or str(job.get("script_id")) != script_id:
        raise HTTPException(status_code=404, detail="找不到该生成任务")
    script = project_script(services, script_id, user)
    if str(job.get("project_id") or "") != str(script.get("project_id") or ""):
        raise HTTPException(status_code=404, detail="找不到该生成任务")
    return job
