from __future__ import annotations

import json
from typing import Annotated, Any, Callable

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from .access import project_script, require_project
from .dependencies import CurrentUser, ServicesDep
from .schemas import TextGenerationRunRequest
from .smart_script_import import (
    MAX_SMART_IMPORT_FILES,
    MAX_SMART_IMPORT_UPLOAD_BYTES,
    SmartImportSource,
    SmartScriptImport,
    SmartScriptImportError,
)
from .uploads import ScriptStorage
from ..error_utils import safe_exception_summary
from ..storage import UPLOAD_CHUNK_SIZE, safe_filename


router = APIRouter(prefix="/api")


@router.get("/projects/{project_id}/text-generation-runs")
def list_project_text_runs(
    project_id: str,
    user: CurrentUser,
    services: ServicesDep,
    script_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    require_project(services, user, project_id)
    if script_id:
        script = project_script(services, script_id, user)
        if str(script.get("project_id") or "") != project_id:
            raise HTTPException(status_code=404, detail="找不到台本")
    rows = services.database.text_generation_runs.list_for_project(
        project_id, script_id=script_id, limit=limit
    )
    return {"runs": [_run_payload(row) for row in rows]}


@router.get("/text-generation-runs/{run_id}")
def get_text_run(
    run_id: str, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    row = services.database.text_generation_runs.get(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="找不到文本生成记录")
    require_project(services, user, str(row["project_id"] or ""))
    return {"run": _run_payload(row)}


@router.post("/text-generation-runs/{run_id}/restore-script-import")
def restore_script_import_run(
    run_id: str, user: CurrentUser, services: ServicesDep
) -> dict[str, Any]:
    row = services.database.text_generation_runs.get(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="找不到文本生成记录")
    require_project(services, user, str(row["project_id"] or ""))
    if row.get("kind") != "script_import":
        raise HTTPException(status_code=422, detail="该记录不是智能导入分析")
    if row.get("status") != "completed":
        raise HTTPException(status_code=409, detail="智能导入分析尚未完成")
    result = _json(row.get("result_json"))
    batch = result.get("batch") if isinstance(result, dict) else None
    if not isinstance(batch, dict):
        raise HTTPException(status_code=409, detail="该记录没有可恢复的导入预览")
    try:
        restored = SmartScriptImport(services).restore_batch(
            str(row["project_id"]), str(user["id"]), batch
        )
    except SmartScriptImportError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"batch": restored}


@router.get("/projects/{project_id}/context-revisions")
def list_project_context_revisions(
    project_id: str,
    user: CurrentUser,
    services: ServicesDep,
    limit: int = 100,
) -> dict[str, Any]:
    require_project(services, user, project_id)
    return {
        "revisions": [
            _revision_payload(row)
            for row in services.database.context_revisions.list_for_project(
                project_id, limit
            )
        ]
    }


@router.get("/scripts/{script_id}/context-revisions")
def list_script_context_revisions(
    script_id: str,
    user: CurrentUser,
    services: ServicesDep,
    limit: int = 100,
) -> dict[str, Any]:
    project_script(services, script_id, user)
    return {
        "revisions": [
            _revision_payload(row)
            for row in services.database.context_revisions.list_for_script(
                script_id, limit
            )
        ]
    }


@router.post("/context-revisions/{revision_id}/restore")
def restore_context_revision(
    revision_id: str,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    revision = services.database.context_revisions.get(revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="找不到上下文版本")
    project = require_project(
        services,
        user,
        str(revision["project_id"]),
        manage=str(revision["scope"]) == "project",
    )
    scope = str(revision["scope"])
    content = str(revision.get("content") or "")
    with services.job_mutation_lock:
        if scope == "project":
            services.database.projects.update(
                str(project["id"]),
                str(project["name"]),
                str(project.get("description") or ""),
                content,
            )
        elif scope == "script":
            script_id = str(revision.get("script_id") or "")
            script = project_script(services, script_id, user)
            try:
                script_version = int(script.get("version") or 1)
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(status_code=409, detail="台本记录损坏，无法恢复") from None
            if not services.database.scripts.update(
                script_id, str(script["name"]), content, expected_version=script_version
            ):
                raise HTTPException(status_code=409, detail="上下文恢复失败，请刷新后重试")
        else:
            raise HTTPException(status_code=409, detail="上下文版本类型无效")
        services.database.context_revisions.record(
            scope=scope,
            project_id=str(revision["project_id"]),
            script_id=str(revision.get("script_id") or "") or None,
            content=content,
            created_by=str(user["id"]),
        )
    services.database.audit.record(
        "context.revision_restored",
        actor=user,
        target_type="context_revision",
        target_id=revision_id,
        project_id=str(revision["project_id"]),
        ip_address=request.client.host if request.client else "",
        details={"scope": scope, "version": revision["version"]},
    )
    if scope == "project":
        return {
            "project": services.database.projects.get(str(project["id"])) or project
        }
    return {"script": services.database.scripts.get(str(revision["script_id"]))}


@router.post("/projects/{project_id}/text-generation-runs", status_code=202)
def create_project_text_run(
    project_id: str,
    changes: TextGenerationRunRequest,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    project = require_project(services, user, project_id, manage=True)
    if changes.kind != "prompt_suggestion":
        raise HTTPException(status_code=422, detail="项目文本任务类型无效")
    context = {"project_prompt": str(project.get("prompt") or ""), "script_prompt": ""}
    parent = _parent_run(services, changes.parent_run_id, project_id, None)
    parent_output = _parent_output(parent)
    if parent:
        context["parent_run_id"] = str(parent["id"])
        if parent_output:
            context["parent_output"] = parent_output
    input_data = {
        "kind": changes.kind,
        "goal": changes.goal,
        "parent_run_id": changes.parent_run_id,
    }
    run = services.database.text_generation_runs.create(
        project_id=project_id,
        script_id=None,
        parent_run_id=str(parent["id"]) if parent else None,
        created_by=str(user["id"]),
        kind=changes.kind,
        input_data=input_data,
        context=context,
    )

    def operation(progress: Callable[..., None]) -> dict[str, str]:
        suggestion = services.text_generation.suggest_prompt(
            scope="project",
            project_prompt=context["project_prompt"],
            script_prompt="",
            goal=_with_parent(changes.goal, parent_output),
            progress=progress,
        )
        return {"suggestion": suggestion}

    _submit(services, run, operation)
    _audit(services, request, user, run, project_id)
    return {
        "run": _run_payload(
            services.database.text_generation_runs.get(run["id"]) or run
        )
    }


@router.post("/projects/{project_id}/script-imports/analyze-run", status_code=202)
async def create_script_import_text_run(
    project_id: str,
    files: Annotated[list[UploadFile], File(...)],
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    project = require_project(services, user, project_id)
    try:
        sources = await _read_smart_import_sources(files)
        if SmartScriptImport(services).pending(project_id, str(user["id"])) is not None:
            raise SmartScriptImportError("已有一批台本等待确认，请先确认或放弃后再导入")
    except SmartScriptImportError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    input_data = {
        "kind": "script_import",
        "source_files": [source.filename for source in sources],
        "source_file_count": len(sources),
        "total_bytes": sum(len(source.content) for source in sources),
    }
    context = {
        "project_prompt": str(project.get("prompt") or ""),
        "source_files": [source.filename for source in sources],
    }
    run = services.database.text_generation_runs.create(
        project_id=project_id,
        script_id=None,
        created_by=str(user["id"]),
        kind="script_import",
        input_data=input_data,
        context=context,
    )

    def operation(progress: Callable[..., None]) -> dict[str, Any]:
        batch = SmartScriptImport(services).analyze(
            project,
            str(user["id"]),
            sources,
            progress=progress,
        )
        return {"batch": batch}

    _submit(services, run, operation)
    _audit(services, request, user, run, project_id)
    current = services.database.text_generation_runs.get(run["id"]) or run
    return {"run": _run_payload(current)}


@router.post("/scripts/{script_id}/text-generation-runs", status_code=202)
def create_script_text_run(
    script_id: str,
    changes: TextGenerationRunRequest,
    request: Request,
    user: CurrentUser,
    services: ServicesDep,
) -> dict[str, Any]:
    script = project_script(services, script_id, user)
    project = require_project(services, user, str(script["project_id"]))
    parent = _parent_run(services, changes.parent_run_id, str(project["id"]), script_id)
    parent_output = _parent_output(parent)
    if (
        changes.version is not None
        and int(script.get("version") or 1) != changes.version
    ):
        raise HTTPException(status_code=409, detail="台本已被其他用户修改，请刷新后重试")
    operation, context, input_data = _script_operation(
        services, script, project, changes, parent_output
    )
    if parent:
        context["parent_run_id"] = str(parent["id"])
        if parent_output:
            context["parent_output"] = parent_output
        input_data["parent_run_id"] = str(parent["id"])
    run = services.database.text_generation_runs.create(
        project_id=str(project["id"]),
        script_id=script_id,
        parent_run_id=str(parent["id"]) if parent else None,
        created_by=str(user["id"]),
        kind=changes.kind,
        input_data=input_data,
        context=context,
    )
    _submit(services, run, operation)
    _audit(services, request, user, run, str(project["id"]))
    current = services.database.text_generation_runs.get(run["id"]) or run
    return {"run": _run_payload(current)}


def _script_operation(
    services: ServicesDep,
    script: dict[str, Any],
    project: dict[str, Any],
    changes: TextGenerationRunRequest,
    parent_output: str = "",
) -> tuple[Callable[..., Any], dict[str, Any], dict[str, Any]]:
    project_prompt = str(project.get("prompt") or "")
    script_prompt = str(script.get("prompt") or "")
    context: dict[str, Any] = {
        "project_prompt": project_prompt,
        "script_prompt": script_prompt,
        "script_version": str(script.get("version") or 1),
    }
    if changes.kind == "prompt_suggestion":
        input_data = {"kind": changes.kind, "goal": changes.goal}

        def operation(progress: Callable[..., None]) -> dict[str, str]:
            return {
                "suggestion": services.text_generation.suggest_prompt(
                    scope="script",
                    project_prompt=project_prompt,
                    script_prompt=script_prompt,
                    goal=_with_parent(changes.goal, parent_output),
                    progress=progress,
                )
            }

        return operation, context, input_data
    if changes.kind == "lines":
        input_data = {
            "kind": changes.kind,
            "instruction": changes.instruction,
            "line_count": changes.line_count,
            "model_id": changes.model_id,
        }

        def operation(progress: Callable[..., None]) -> dict[str, Any]:
            return {
                "lines": services.text_generation.generate_lines(
                    project_prompt=project_prompt,
                    script_prompt=script_prompt,
                    instruction=_with_parent(changes.instruction, parent_output),
                    line_count=changes.line_count,
                    model_id=changes.model_id,
                    progress=progress,
                )
            }

        return operation, context, input_data
    if changes.kind == "rewrite_line":
        if (
            not changes.sequence
            or not changes.text.strip()
            or not changes.instruction.strip()
        ):
            raise HTTPException(status_code=422, detail="单行修改参数不完整")
        items = ScriptStorage(services).load_items(script)
        if not any(int(item.order) == changes.sequence for item in items):
            raise HTTPException(status_code=404, detail="找不到对应台词行")
        input_data = {
            "kind": changes.kind,
            "sequence": changes.sequence,
            "text": changes.text,
            "pronunciation": changes.pronunciation,
            "instruction": changes.instruction,
            "version": changes.version,
        }

        def operation(progress: Callable[..., None]) -> dict[str, Any]:
            return {
                "line": services.text_generation.rewrite_line(
                    project_prompt=project_prompt,
                    script_prompt=script_prompt,
                    text=changes.text,
                    pronunciation=changes.pronunciation,
                    instruction=_with_parent(changes.instruction, parent_output),
                    progress=progress,
                )
            }

        return operation, context, input_data
    if changes.kind == "pronunciations":
        items = ScriptStorage(services).load_items(script)
        if not items:
            raise HTTPException(status_code=422, detail="台本没有可生成的台词")
        model_items = [
            {
                "sequence": item.order,
                "text": item.text,
                "pronunciation": item.pronunciation,
                "rewrite_instruction": item.rewrite_instruction,
            }
            for item in items
        ]
        input_data = {
            "kind": changes.kind,
            "model_id": changes.model_id,
            "version": changes.version,
            "line_count": len(model_items),
        }
        context["items"] = model_items

        def operation(progress: Callable[..., None]) -> dict[str, Any]:
            return {
                "lines": services.text_generation.generate_pronunciations(
                    project_prompt=project_prompt,
                    script_prompt=script_prompt,
                    items=model_items,
                    model_id=changes.model_id,
                    progress=progress,
                )
            }

        return operation, context, input_data
    raise HTTPException(status_code=422, detail="文本任务类型无效")


def _submit(
    services: ServicesDep, run: dict[str, Any], operation: Callable[..., Any]
) -> None:
    try:
        services.text_generation_runner.submit(str(run["id"]), operation)
    except RuntimeError as error:
        services.database.text_generation_runs.fail(
            str(run["id"]), error=safe_exception_summary(error, "文本生成队列不可用")
        )
        raise HTTPException(status_code=429, detail=str(error)) from error


def _audit(
    services: ServicesDep,
    request: Request,
    user: dict[str, Any],
    run: dict[str, Any],
    project_id: str,
) -> None:
    services.database.audit.record(
        "text_generation.queued",
        actor=user,
        target_type="text_generation_run",
        target_id=str(run["id"]),
        project_id=project_id,
        ip_address=request.client.host if request.client else "",
        details={"kind": run["kind"]},
    )


def _run_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "script_id": row.get("script_id"),
        "parent_run_id": row.get("parent_run_id"),
        "kind": row["kind"],
        "status": row["status"],
        "stage": row["stage"],
        "input": _json(row.get("input_json")),
        "context": _json(row.get("context_json")),
        "output_text": row.get("output_text") or "",
        "result": _json(row.get("result_json")),
        "error": row.get("error") or "",
        "created_by_name": row.get("created_by_name")
        or row.get("created_by_username")
        or "",
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "updated_at": row.get("updated_at"),
        "finished_at": row.get("finished_at"),
    }


def _revision_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "project_id": row["project_id"],
        "script_id": row.get("script_id"),
        "version": row["version"],
        "content": row["content"],
        "created_by_name": row.get("created_by_name")
        or row.get("created_by_username")
        or "",
        "created_at": row["created_at"],
    }


def _json(value: Any) -> Any:
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}


def _parent_run(
    services: ServicesDep,
    run_id: str | None,
    project_id: str,
    script_id: str | None,
) -> dict[str, Any] | None:
    if not run_id:
        return None
    run = services.database.text_generation_runs.get(run_id)
    if (
        not run
        or str(run.get("project_id") or "") != project_id
        or (
            (script_id is None and run.get("script_id") is not None)
            or (script_id is not None and str(run.get("script_id") or "") != script_id)
        )
        or run.get("status") != "completed"
    ):
        raise HTTPException(status_code=422, detail="找不到可继续迭代的文本生成记录")
    return run


def _parent_output(run: dict[str, Any] | None) -> str:
    if not run:
        return ""
    result = _json(run.get("result_json"))
    if isinstance(result, dict):
        value = _output_from_result(result)
        if value:
            return value[:12000]
    return str(run.get("output_text") or "")[:12000]


def _output_from_result(result: dict[str, Any]) -> str:
    for key in ("suggestion", "text"):
        if isinstance(result.get(key), str):
            return result[key]
    if isinstance(result.get("lines"), list):
        return "\n".join(
            str(item.get("text") or item.get("pronunciation") or "")
            for item in result["lines"]
            if isinstance(item, dict)
        )
    line = result.get("line")
    return str(line.get("text") or "") if isinstance(line, dict) else ""


def _with_parent(instruction: str, parent_output: str) -> str:
    if not parent_output:
        return instruction
    return f"{instruction.strip() or '请继续优化上一轮结果。'}\n\n" f"【上一轮生成结果】\n{parent_output}"


async def _read_smart_import_sources(
    files: list[UploadFile],
) -> list[SmartImportSource]:
    if not files:
        raise SmartScriptImportError("请至少选择一个台本文件")
    if len(files) > MAX_SMART_IMPORT_FILES:
        raise SmartScriptImportError(f"一次最多导入 {MAX_SMART_IMPORT_FILES} 个文件")
    remaining = MAX_SMART_IMPORT_UPLOAD_BYTES
    sources: list[SmartImportSource] = []
    for upload in files:
        chunks: list[bytes] = []
        total = 0
        while chunk := await upload.read(
            max(1, min(UPLOAD_CHUNK_SIZE, remaining - total + 1))
        ):
            total += len(chunk)
            if total > remaining:
                raise SmartScriptImportError("本次上传文件总大小不能超过 20 MB")
            chunks.append(chunk)
        sources.append(
            SmartImportSource(
                filename=safe_filename(upload.filename or "台本.txt", "台本.txt"),
                content=b"".join(chunks),
            )
        )
        remaining -= total
    return sources
