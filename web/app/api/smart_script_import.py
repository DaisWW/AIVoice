from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain import MAX_SCRIPT_ITEMS, ScriptItem
from ..script_parser import (
    SUPPORTED_SCRIPT_EXTENSIONS,
    ScriptFormatError,
    build_script_item,
    validate_docx_archive,
)
from ..services import ApplicationServices
from ..storage import ensure_within, safe_filename


MAX_SMART_IMPORT_FILES = 20
MAX_SMART_IMPORT_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SMART_IMPORT_CHARACTERS = 120_000
MAX_SMART_IMPORT_UNITS = 800
MAX_SMART_IMPORT_DRAFTS = 800
MAX_SMART_IMPORT_ANALYSIS_CHARACTERS = 8_000
MAX_SMART_IMPORT_ANALYSIS_UNITS = 40
_PRONUNCIATION_MARKER_RE = re.compile(r"^【\s*(?:\d+\s*)?发音\s*】\s*(.*?)\s*$")


class SmartScriptImportError(ValueError):
    pass


class _CorruptSmartImportManifest(SmartScriptImportError):
    """The transient preview cannot be parsed."""


@dataclass(frozen=True)
class SmartImportSource:
    filename: str
    content: bytes


@dataclass(frozen=True)
class _SmartImportUnit:
    id: int
    filename: str
    text: str
    existing_segment: bool
    pronunciation: str = ""
    context: str = ""


class SmartScriptImport:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services

    def analyze(
        self,
        project: dict[str, Any],
        user_id: str,
        sources: list[SmartImportSource],
    ) -> dict[str, Any]:
        project_id = str(project["id"])
        if self.pending(project_id, user_id) is not None:
            raise SmartScriptImportError("已有一批台本等待确认，请先确认或放弃后再导入")
        units = _extract_smart_import_units(sources)
        segments: list[dict[str, Any]] = []
        for chunk in _smart_import_analysis_chunks(units):
            analysis = self._services.text_generation.analyze_script_import(
                project_prompt=str(project.get("prompt") or ""),
                units=[
                    {
                        "unit_id": unit.id,
                        "source_file": unit.filename,
                        "existing_segment": unit.existing_segment,
                        "length": len(unit.text),
                        "content": unit.text,
                        "context": unit.context,
                    }
                    for unit in chunk
                ],
            )
            segments.extend(_validate_smart_import_segments(chunk, analysis))
        batch = _build_smart_import_batch(
            sources=[safe_filename(source.filename, "台本.txt") for source in sources],
            units=units,
            segments=segments,
        )
        manifest = {
            "version": 1,
            "project_id": project_id,
            "user_id": user_id,
            "batch": batch,
        }
        with self._services.job_mutation_lock:
            with self._services.script_write_lock:
                if self._load_manifest(project_id, user_id) is not None:
                    raise SmartScriptImportError("已有一批台本等待确认，请先确认或放弃后再导入")
                self._write_manifest(project_id, user_id, manifest)
        return batch

    def pending(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        with self._services.script_write_lock:
            try:
                manifest = self._load_manifest(project_id, user_id)
            except _CorruptSmartImportManifest as error:
                raise SmartScriptImportError("待确认台本数据已损坏，请清理后重新上传分析") from error
        return dict(manifest["batch"]) if manifest is not None else None

    def confirm(
        self,
        project_id: str,
        user_id: str,
        batch_id: str,
        selections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with self._services.job_mutation_lock:
            with self._services.script_write_lock:
                manifest = self._require_manifest(project_id, user_id, batch_id)
                prepared = _prepare_smart_import_confirmation(
                    manifest["batch"], selections
                )
                scripts: list[dict[str, Any]] = []
                for draft, name, items in prepared:
                    script_id = _smart_import_script_id(batch_id, str(draft["id"]))
                    script = self._services.database.scripts.get(script_id)
                    if script is None:
                        script = self._create_script(
                            project_id, user_id, script_id, name, items
                        )
                    elif (
                        str(script.get("project_id") or "") != project_id
                        or str(script.get("owner_id") or "") != user_id
                        or str(script.get("source_kind") or "") != "ai_import"
                    ):
                        raise SmartScriptImportError("待确认台本与现有数据冲突，请联系管理员")
                    elif str(script.get("name") or "") != name:
                        raise SmartScriptImportError("本次确认已部分完成，台本名称已发生变化，请使用原名称重试")
                    scripts.append(script)
                self._manifest_path(project_id, user_id).unlink(missing_ok=True)
        return scripts

    def discard(self, project_id: str, user_id: str, batch_id: str) -> None:
        with self._services.job_mutation_lock:
            with self._services.script_write_lock:
                path = self._manifest_path(project_id, user_id)
                self._require_manifest(project_id, user_id, batch_id)
                path.unlink(missing_ok=True)

    def discard_corrupt(self, project_id: str, user_id: str) -> None:
        """Explicitly remove an unreadable preview after the user confirms it."""
        with self._services.job_mutation_lock:
            with self._services.script_write_lock:
                path = self._manifest_path(project_id, user_id)
                try:
                    manifest = self._load_manifest(project_id, user_id)
                except _CorruptSmartImportManifest:
                    path.unlink(missing_ok=True)
                    return
                if manifest is None:
                    raise SmartScriptImportError("没有需要清理的待确认台本")
                raise SmartScriptImportError("待确认台本仍然有效，请通过正常流程放弃")

    def _create_script(
        self,
        project_id: str,
        user_id: str,
        script_id: str,
        name: str,
        items: list[ScriptItem],
    ) -> dict[str, Any]:
        try:
            directory = ensure_within(
                self._services.settings.script_upload_root / project_id,
                self._services.settings.root,
            )
        except ValueError as error:
            raise SmartScriptImportError("项目路径无效，无法保存导入台本") from error
        directory.mkdir(parents=True, exist_ok=True)
        target = ensure_within(
            directory / f"{script_id}.ai-import.csv",
            self._services.settings.root,
        )
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
        file_published = False
        database_created = False
        try:
            _write_script_csv(temporary, items)
            if target.exists() or target.is_symlink():
                os.replace(target, backup)
            os.replace(temporary, target)
            file_published = True
            self._services.database.scripts.create(
                name,
                safe_filename(f"{name}.csv", "ai-import.csv"),
                target,
                user_id,
                "ai_import",
                items,
                script_id=script_id,
                project_id=project_id,
            )
            database_created = True
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if file_published and not database_created:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            if backup.exists():
                try:
                    os.replace(backup, target)
                except OSError:
                    pass
            raise
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            # The database row and generated file are already durable.
            pass
        script = self._services.database.scripts.get(script_id)
        if script is None:  # pragma: no cover - guarded by repository create
            raise RuntimeError("智能导入台本创建后未找到")
        return script

    def _require_manifest(
        self, project_id: str, user_id: str, batch_id: str
    ) -> dict[str, Any]:
        try:
            manifest = self._load_manifest(project_id, user_id)
        except _CorruptSmartImportManifest as error:
            raise SmartScriptImportError("待确认台本数据已损坏，请重新上传分析") from error
        if manifest is None or str(manifest["batch"].get("batch_id") or "") != batch_id:
            raise SmartScriptImportError("找不到这批待确认台本，请重新上传分析")
        return manifest

    def _load_manifest(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        path = self._manifest_path(project_id, user_id)
        if not path.is_file():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise _CorruptSmartImportManifest("待确认台本数据无法读取，请放弃后重新导入") from error
        if not isinstance(manifest, dict):
            raise _CorruptSmartImportManifest("待确认台本数据格式无效")
        if (
            str(manifest.get("project_id") or "") != project_id
            or str(manifest.get("user_id") or "") != user_id
        ):
            raise _CorruptSmartImportManifest("待确认台本不属于当前项目或用户")
        if manifest.get("version") != 1 or not isinstance(manifest.get("batch"), dict):
            raise _CorruptSmartImportManifest("待确认台本数据格式无效")
        _validate_smart_import_batch(manifest["batch"])
        return manifest

    def _write_manifest(
        self, project_id: str, user_id: str, manifest: dict[str, Any]
    ) -> None:
        path = self._manifest_path(project_id, user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def _manifest_path(self, project_id: str, user_id: str) -> Path:
        path = (
            self._services.settings.data_root
            / "script-imports"
            / safe_filename(project_id, "project")
            / safe_filename(user_id, "user")
            / "pending.json"
        )
        return ensure_within(path, self._services.settings.data_root)


def _extract_smart_import_units(
    sources: list[SmartImportSource],
) -> list[_SmartImportUnit]:
    if not sources:
        raise SmartScriptImportError("请至少选择一个台本文件")
    if len(sources) > MAX_SMART_IMPORT_FILES:
        raise SmartScriptImportError(f"一次最多导入 {MAX_SMART_IMPORT_FILES} 个文件")
    if sum(len(source.content) for source in sources) > MAX_SMART_IMPORT_UPLOAD_BYTES:
        raise SmartScriptImportError("本次上传文件总大小不能超过 20 MB")

    values: list[tuple[str, str, bool, str, str]] = []
    for source in sources:
        filename = safe_filename(source.filename, "台本.txt")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SCRIPT_EXTENSIONS:
            raise SmartScriptImportError("智能导入仅支持 .txt、.md、.csv、.docx")
        if suffix == ".docx":
            extracted = _extract_docx_unit_values(filename, source.content)
        elif suffix == ".csv":
            extracted = _extract_csv_unit_values(filename, source.content)
        else:
            extracted = _extract_text_unit_values(filename, source.content)
        if not extracted:
            raise SmartScriptImportError(f"文件“{filename}”没有可识别的非空文本")
        values.extend(extracted)

    character_count = sum(len(value[1]) for value in values)
    if character_count > MAX_SMART_IMPORT_CHARACTERS:
        raise SmartScriptImportError(
            f"本次可识别文本不能超过 {MAX_SMART_IMPORT_CHARACTERS} 个字符，请分批导入"
        )
    if len(values) > MAX_SMART_IMPORT_UNITS:
        raise SmartScriptImportError("本次识别单元过多，请分批导入")
    return [
        _SmartImportUnit(index, *value) for index, value in enumerate(values, start=1)
    ]


def _decode_smart_import_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SmartScriptImportError("文本台本必须使用 UTF-8 编码") from error


def _smart_import_analysis_chunks(
    units: list[_SmartImportUnit],
) -> list[list[_SmartImportUnit]]:
    chunks: list[list[_SmartImportUnit]] = []
    current: list[_SmartImportUnit] = []
    character_count = 0
    for unit in units:
        would_overflow = current and (
            len(current) >= MAX_SMART_IMPORT_ANALYSIS_UNITS
            or character_count + len(unit.text) > MAX_SMART_IMPORT_ANALYSIS_CHARACTERS
        )
        if would_overflow:
            chunks.append(current)
            current = []
            character_count = 0
        current.append(unit)
        character_count += len(unit.text)
    if current:
        chunks.append(current)
    return chunks


def _extract_text_unit_values(
    filename: str, content: bytes
) -> list[tuple[str, str, bool, str, str]]:
    lines = [line.strip() for line in _decode_smart_import_text(content).splitlines()]
    lines = [line for line in lines if line]
    existing_segments = len(lines) > 1
    result: list[tuple[str, str, bool, str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = _PRONUNCIATION_MARKER_RE.match(line)
        if marker:
            if index + 1 >= len(lines):
                raise SmartScriptImportError("发音标记后缺少对应台词")
            result.append(
                (filename, lines[index + 1], True, marker.group(1).strip(), "")
            )
            index += 2
            continue
        delimiter = "|" if "|" in line else "\t" if "\t" in line else ""
        if delimiter:
            text, pronunciation = line.split(delimiter, 1)
            if text.strip() or pronunciation.strip():
                result.append(
                    (
                        filename,
                        text.strip() or pronunciation.strip(),
                        True,
                        pronunciation.strip(),
                        "",
                    )
                )
        else:
            result.append(
                (
                    filename,
                    line,
                    existing_segments or not _needs_smart_text_split(line),
                    "",
                    "",
                )
            )
        index += 1
    return result


def _needs_smart_text_split(text: str) -> bool:
    return len(text) >= 120 or text.count("：") + text.count(":") >= 2


def _extract_csv_unit_values(
    filename: str, content: bytes
) -> list[tuple[str, str, bool, str, str]]:
    text = _decode_smart_import_text(content)
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as error:
        raise SmartScriptImportError(f"CSV 文件“{filename}”格式无效") from error
    if not rows:
        return []
    headers = [str(value or "").strip() for value in rows[0]]
    normalized = [header.lower() for header in headers]
    text_index = next(
        (
            index
            for index, header in enumerate(normalized)
            if header in {"text", "script", "台词", "正常台词", "dialogue"}
        ),
        None,
    )
    pronunciation_index = next(
        (
            index
            for index, header in enumerate(normalized)
            if header in {"pronunciation", "pronounce", "发音", "音标"}
        ),
        None,
    )
    result: list[tuple[str, str, bool, str, str]] = []
    if text_index is not None:
        for row in rows[1:]:
            dialogue = row[text_index].strip() if text_index < len(row) else ""
            if not dialogue:
                continue
            pronunciation = (
                row[pronunciation_index].strip()
                if pronunciation_index is not None and pronunciation_index < len(row)
                else ""
            )
            context = "；".join(
                f"{headers[index] or f'列 {index + 1}'}：{value.strip()}"
                for index, value in enumerate(row)
                if index not in {text_index, pronunciation_index} and value.strip()
            )
            result.append((filename, dialogue, True, pronunciation, context))
        return result

    for row_number, row in enumerate(rows, start=1):
        for column_number, value in enumerate(row, start=1):
            cell = value.strip()
            if not cell:
                continue
            header = headers[column_number - 1] if column_number <= len(headers) else ""
            context = f"CSV 第 {row_number} 行，{header or f'第 {column_number} 列'}"
            result.append((filename, cell, True, "", context))
    return result


def _extract_docx_unit_values(
    filename: str, content: bytes
) -> list[tuple[str, str, bool, str, str]]:
    try:
        validate_docx_archive(content)
    except ScriptFormatError as error:
        raise SmartScriptImportError(str(error)) from error
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as error:  # pragma: no cover - dependency is optional
        raise SmartScriptImportError("当前环境未安装 python-docx") from error
    try:
        document = Document(io.BytesIO(content))
    except Exception as error:
        raise SmartScriptImportError(f"DOCX 文件“{filename}”损坏或无效") from error

    result: list[tuple[str, str, bool, str, str]] = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text.strip()
            if text:
                result.append((filename, text, True, "", "DOCX 段落"))
            continue
        if not child.tag.endswith("}tbl"):
            continue
        table = Table(child, document)
        for row_number, row in enumerate(table.rows, start=1):
            for column_number, cell in enumerate(row.cells, start=1):
                text = cell.text.strip()
                if text:
                    context = f"DOCX 表格第 {row_number} 行第 {column_number} 列"
                    result.append((filename, text, True, "", context))
    return result


def _validate_smart_import_segments(
    units: list[_SmartImportUnit], analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    raw_segments = analysis.get("segments") if isinstance(analysis, dict) else None
    if not isinstance(raw_segments, list) or not raw_segments:
        raise SmartScriptImportError("AI 没有返回有效的台本分段")
    if len(raw_segments) > MAX_SCRIPT_ITEMS:
        raise SmartScriptImportError("AI 分段数量过多，请分批导入")

    units_by_id = {unit.id: unit for unit in units}
    by_unit: dict[int, list[dict[str, Any]]] = {unit.id: [] for unit in units}
    normalized: list[dict[str, Any]] = []
    last_order: tuple[int, int] | None = None
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise SmartScriptImportError("AI 返回的台本分段格式无效")
        unit_id = _smart_import_integer(raw.get("unit_id"), "原文单元编号")
        unit = units_by_id.get(unit_id)
        if unit is None:
            raise SmartScriptImportError("AI 返回了越界的原文单元编号")
        start = _smart_import_integer(raw.get("start"), "分段起点")
        end = _smart_import_integer(raw.get("end"), "分段终点")
        content_start = _smart_import_integer(raw.get("content_start", start), "台词起点")
        if not 0 <= start < end <= len(unit.text) or not start <= content_start < end:
            raise SmartScriptImportError("AI 返回的字符区间无效")
        order = (unit_id, start)
        if last_order is not None and order <= last_order:
            raise SmartScriptImportError("AI 返回的字符区间有遗漏、重叠或乱序")
        last_order = order
        kind = str(raw.get("kind") or "").strip()
        if kind not in {"dialogue", "note"}:
            raise SmartScriptImportError("AI 返回了不支持的内容类型")
        script = _smart_import_label(raw.get("script"), "台本名称")
        speaker = _smart_import_label(raw.get("speaker"), "角色名称")
        if kind == "note" and content_start != start:
            raise SmartScriptImportError("非台词内容不能设置台词起点")
        if kind == "dialogue" and content_start > start:
            prefix = unit.text[start:content_start].rstrip()
            if not speaker or speaker not in prefix or not prefix.endswith((":", "：")):
                raise SmartScriptImportError("AI 返回的角色标签不是原文中的准确标签")
        segment = {
            "unit_id": unit_id,
            "start": start,
            "end": end,
            "content_start": content_start,
            "script": script,
            "speaker": speaker,
            "kind": kind,
        }
        normalized.append(segment)
        by_unit[unit_id].append(segment)

    for unit in units:
        cursor = 0
        segments = by_unit[unit.id]
        for segment in segments:
            if segment["start"] != cursor:
                raise SmartScriptImportError("AI 返回的字符区间有遗漏、重叠或乱序")
            cursor = int(segment["end"])
        if cursor != len(unit.text):
            raise SmartScriptImportError("AI 未完整归类原文，已停止导入以避免内容丢失")
        if unit.existing_segment and (
            len(segments) != 1
            or segments[0]["start"] != 0
            or segments[0]["end"] != len(unit.text)
        ):
            raise SmartScriptImportError("AI 尝试拆分已有分段，已停止导入")
    return normalized


def _build_smart_import_batch(
    *,
    sources: list[str],
    units: list[_SmartImportUnit],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    units_by_id = {unit.id: unit for unit in units}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    excluded: list[dict[str, str]] = []
    for segment in segments:
        unit = units_by_id[int(segment["unit_id"])]
        if segment["kind"] == "note":
            text = unit.text[int(segment["start"]) : int(segment["end"])]
            if text.strip():
                excluded.append({"source_file": unit.filename, "text": text})
            continue
        text = unit.text[int(segment["content_start"]) : int(segment["end"])]
        if not text.strip():
            raise SmartScriptImportError("去除角色标签后没有台词")
        if len(text) > 2000:
            raise SmartScriptImportError("单条台词不能超过 2000 个字符")
        script = str(segment["script"] or Path(unit.filename).stem).strip()
        speaker = str(segment["speaker"] or "").strip()
        key = (script, speaker)
        group = groups.setdefault(
            key,
            {
                "script": script,
                "speaker": speaker,
                "source_files": [],
                "lines": [],
            },
        )
        if unit.filename not in group["source_files"]:
            group["source_files"].append(unit.filename)
        group["lines"].append(
            {
                "text": text,
                "pronunciation": unit.pronunciation or text,
                "source_file": unit.filename,
            }
        )
    if not groups:
        raise SmartScriptImportError("AI 没有识别出可导入的台词")
    if len(groups) > MAX_SMART_IMPORT_DRAFTS:
        raise SmartScriptImportError("识别出的台本数量过多，请分批导入")

    drafts: list[dict[str, Any]] = []
    for index, group in enumerate(groups.values(), start=1):
        name = (
            " · ".join(
                value
                for value in (str(group["script"]), str(group["speaker"]))
                if value
            )
            or f"导入台本 {index}"
        )
        drafts.append(
            {
                "id": f"draft-{index}",
                "name": name[:80],
                "script": group["script"],
                "speaker": group["speaker"],
                "source_files": group["source_files"],
                "line_count": len(group["lines"]),
                "lines": group["lines"],
            }
        )
    dialogue_line_count = sum(int(draft["line_count"]) for draft in drafts)
    if dialogue_line_count > MAX_SCRIPT_ITEMS:
        raise SmartScriptImportError(f"本次最多确认 {MAX_SCRIPT_ITEMS} 条台词，请分批导入")
    return {
        "batch_id": f"import-{uuid.uuid4().hex[:16]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": sources,
        "drafts": drafts,
        "excluded": excluded,
        "dialogue_line_count": dialogue_line_count,
        "excluded_count": len(excluded),
    }


def _prepare_smart_import_confirmation(
    batch: dict[str, Any], selections: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], str, list[ScriptItem]]]:
    raw_drafts = batch.get("drafts")
    if not isinstance(raw_drafts, list):
        raise SmartScriptImportError("待确认台本数据格式无效")
    if not isinstance(selections, list):
        raise SmartScriptImportError("待确认台本选择无效，请刷新后重试")
    drafts = {
        str(draft.get("id") or ""): draft
        for draft in raw_drafts
        if isinstance(draft, dict)
    }
    prepared: list[tuple[dict[str, Any], str, list[ScriptItem]]] = []
    seen: set[str] = set()
    total_items = 0
    for selection in selections:
        if not isinstance(selection, dict):
            raise SmartScriptImportError("待确认台本选择无效，请刷新后重试")
        draft_id = str(selection.get("id") or "")
        draft = drafts.get(draft_id)
        if draft is None or draft_id in seen:
            raise SmartScriptImportError("待确认台本选择无效，请刷新后重试")
        seen.add(draft_id)
        name = str(selection.get("name") or "").strip()
        if not name or len(name) > 80:
            raise SmartScriptImportError("台本名称需为 1-80 个字符")
        raw_lines = draft.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            raise SmartScriptImportError("待确认台本没有有效台词")
        items: list[ScriptItem] = []
        for line_number, line in enumerate(raw_lines, start=1):
            if not isinstance(line, dict):
                raise SmartScriptImportError("待确认台本数据格式无效")
            text = str(line.get("text") or "")
            pronunciation = str(line.get("pronunciation") or text)
            try:
                item = build_script_item(text, pronunciation, line_number, line_number)
            except ScriptFormatError as error:
                raise SmartScriptImportError(
                    f"台本“{draft.get('name') or name}”仍有发音格式错误: {error}"
                ) from error
            items.append(item)
        total_items += len(items)
        prepared.append((draft, name, items))
    if not prepared:
        raise SmartScriptImportError("请至少选择一个台本")
    if total_items > MAX_SCRIPT_ITEMS:
        raise SmartScriptImportError(f"本次最多确认 {MAX_SCRIPT_ITEMS} 条台词")
    return prepared


def _validate_smart_import_batch(batch: dict[str, Any]) -> None:
    batch_id = batch.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id or len(batch_id) > 64:
        raise _CorruptSmartImportManifest("待确认台本批次编号无效")

    source_files = batch.get("source_files")
    if (
        not isinstance(source_files, list)
        or not source_files
        or any(not isinstance(value, str) or not value for value in source_files)
    ):
        raise _CorruptSmartImportManifest("待确认台本来源文件无效")

    raw_drafts = batch.get("drafts")
    if not isinstance(raw_drafts, list) or not raw_drafts:
        raise _CorruptSmartImportManifest("待确认台本草稿无效")
    if len(raw_drafts) > MAX_SMART_IMPORT_DRAFTS:
        raise _CorruptSmartImportManifest("待确认台本草稿数量过多")

    draft_ids: set[str] = set()
    total_lines = 0
    for draft in raw_drafts:
        if not isinstance(draft, dict):
            raise _CorruptSmartImportManifest("待确认台本草稿格式无效")
        draft_id = draft.get("id")
        if not isinstance(draft_id, str) or not draft_id or draft_id in draft_ids:
            raise _CorruptSmartImportManifest("待确认台本草稿编号无效")
        draft_ids.add(draft_id)
        for key in ("name", "script", "speaker"):
            value = draft.get(key)
            if (
                not isinstance(value, str)
                or len(value) > 80
                or "\n" in value
                or "\r" in value
            ):
                raise _CorruptSmartImportManifest("待确认台本草稿字段无效")
        draft_sources = draft.get("source_files")
        if not isinstance(draft_sources, list) or any(
            not isinstance(value, str) or not value for value in draft_sources
        ):
            raise _CorruptSmartImportManifest("待确认台本草稿来源无效")
        lines = draft.get("lines")
        line_count = draft.get("line_count")
        if (
            not isinstance(lines, list)
            or not lines
            or isinstance(line_count, bool)
            or not isinstance(line_count, int)
            or line_count != len(lines)
        ):
            raise _CorruptSmartImportManifest("待确认台本台词列表无效")
        for line in lines:
            if not isinstance(line, dict):
                raise _CorruptSmartImportManifest("待确认台本台词格式无效")
            text = line.get("text")
            pronunciation = line.get("pronunciation")
            source_file = line.get("source_file")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > 2000
                or not isinstance(pronunciation, str)
                or not isinstance(source_file, str)
                or not source_file
            ):
                raise _CorruptSmartImportManifest("待确认台本台词字段无效")
        total_lines += line_count

    dialogue_line_count = batch.get("dialogue_line_count")
    if (
        isinstance(dialogue_line_count, bool)
        or not isinstance(dialogue_line_count, int)
        or dialogue_line_count != total_lines
        or dialogue_line_count > MAX_SCRIPT_ITEMS
    ):
        raise _CorruptSmartImportManifest("待确认台本台词数量无效")

    excluded = batch.get("excluded")
    excluded_count = batch.get("excluded_count")
    if (
        not isinstance(excluded, list)
        or isinstance(excluded_count, bool)
        or not isinstance(excluded_count, int)
        or excluded_count != len(excluded)
    ):
        raise _CorruptSmartImportManifest("待确认台本排除项无效")
    for entry in excluded:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("source_file"), str)
            or not entry.get("source_file")
            or not isinstance(entry.get("text"), str)
            or not entry.get("text")
        ):
            raise _CorruptSmartImportManifest("待确认台本排除项格式无效")


def _smart_import_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SmartScriptImportError(f"AI 返回的{label}无效")
    return value


def _smart_import_label(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if len(result) > 80 or "\n" in result or "\r" in result:
        raise SmartScriptImportError(f"AI 返回的{label}无效")
    return result


def _smart_import_script_id(batch_id: str, draft_id: str) -> str:
    value = uuid.uuid5(uuid.NAMESPACE_URL, f"voice-lab:{batch_id}:{draft_id}")
    return f"script-{value.hex[:12]}"


def _write_script_csv(path: Path, items: list[ScriptItem]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("text", "pronunciation"))
        writer.writerows((item.text, item.pronunciation) for item in items)
