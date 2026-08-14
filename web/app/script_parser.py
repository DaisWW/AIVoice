from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from voice_core.pronunciation import (
    PronunciationAnalysis,
    PronunciationError,
    analyze_pronunciation,
)

from .domain import ScriptItem


SUPPORTED_SCRIPT_EXTENSIONS = {".txt", ".md", ".csv", ".docx"}
MARKER_RE = re.compile(r"^【\s*(?:\d+\s*)?发音\s*】\s*(.*?)\s*$")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


class ScriptFormatError(ValueError):
    pass


def analyze_script_pronunciation(pronunciation: str) -> PronunciationAnalysis:
    try:
        return analyze_pronunciation(pronunciation)
    except PronunciationError as error:
        raise ScriptFormatError(str(error)) from error


def _item(text: str, pronunciation: str, line_number: int, order: int) -> ScriptItem:
    pronunciation = pronunciation.strip()
    if not pronunciation:
        raise ScriptFormatError(f"第 {line_number} 行缺少发音标记")
    analysis = analyze_script_pronunciation(pronunciation)
    return ScriptItem(
        order=order,
        source_line=line_number,
        text=text.strip() or analysis.generated_text,
        pronunciation=pronunciation,
        generated_text=analysis.generated_text,
        direction=analysis.direction,
        emphasis=analysis.emphasis,
        hold_units=analysis.hold_units,
    )


def _parse_rows(lines: list[str]) -> list[ScriptItem]:
    result: list[ScriptItem] = []
    pending: tuple[int, str] | None = None
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("<!--") or line.startswith("#"):
            continue
        marker = MARKER_RE.match(line)
        if marker:
            pending = (line_number, marker.group(1).strip())
            continue
        if pending is not None:
            marker_line, pronunciation = pending
            result.append(_item(line, pronunciation, marker_line, len(result) + 1))
            pending = None
            continue
        if "|" in line:
            text, pronunciation = line.split("|", 1)
            result.append(_item(text, pronunciation, line_number, len(result) + 1))
            continue
        if "\t" in line:
            text, pronunciation = line.split("\t", 1)
            result.append(_item(text, pronunciation, line_number, len(result) + 1))
            continue
        result.append(_item("", line, line_number, len(result) + 1))
    if pending is not None:
        raise ScriptFormatError(f"第 {pending[0]} 行有发音标记，但缺少对应正常台词")
    if not result:
        raise ScriptFormatError("台本没有可生成的非空行")
    return result


def _parse_csv(content: str) -> list[ScriptItem]:
    reader = csv.DictReader(io.StringIO(content))
    fieldnames = [
        str(value or "").strip().lower() for value in (reader.fieldnames or [])
    ]
    if not fieldnames:
        raise ScriptFormatError("CSV 缺少表头")
    text_key = next(
        (
            reader.fieldnames[i]
            for i, name in enumerate(fieldnames)
            if name in {"text", "script", "台词", "正常台词"}
        ),
        None,
    )
    pronunciation_key = next(
        (
            reader.fieldnames[i]
            for i, name in enumerate(fieldnames)
            if name in {"pronunciation", "pronounce", "发音", "音标"}
        ),
        None,
    )
    if pronunciation_key is None:
        raise ScriptFormatError("CSV 必须包含 pronunciation（或 发音）列")
    result: list[ScriptItem] = []
    for line_number, row in enumerate(reader, start=2):
        pronunciation = str(row.get(pronunciation_key) or "").strip()
        text = str(row.get(text_key) or "").strip() if text_key else ""
        if pronunciation:
            result.append(_item(text, pronunciation, line_number, len(result) + 1))
    if not result:
        raise ScriptFormatError("CSV 没有可生成的发音行")
    return result


def parse_content(content: bytes, suffix: str) -> list[ScriptItem]:
    suffix = suffix.lower()
    if suffix not in SUPPORTED_SCRIPT_EXTENSIONS:
        raise ScriptFormatError("台本仅支持 .txt、.md、.csv、.docx")
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as error:  # pragma: no cover
            raise ScriptFormatError("当前环境未安装 python-docx") from error
        try:
            document = Document(io.BytesIO(content))
        except Exception as error:
            raise ScriptFormatError("DOCX 文件损坏或不是有效的 Word 文档") from error
        return _parse_rows([paragraph.text for paragraph in document.paragraphs])
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ScriptFormatError("文本台本必须使用 UTF-8 编码") from error
    return _parse_csv(text) if suffix == ".csv" else _parse_rows(text.splitlines())


def parse_file(path: Path) -> list[ScriptItem]:
    return parse_content(path.read_bytes(), path.suffix)


def parse_guide(path: Path) -> dict[str, list[ScriptItem]]:
    """Parse the older full-script guide into displayable marked segments."""
    sections: dict[str, list[ScriptItem]] = {}
    current_name: str | None = None
    pending: tuple[int, str, list[str]] | None = None

    def finalize() -> None:
        nonlocal pending
        if pending is None or current_name is None:
            pending = None
            return
        line_number, pronunciation, text_lines = pending
        item = _item(
            "\n".join(text_lines),
            pronunciation,
            line_number,
            len(sections.setdefault(current_name, [])) + 1,
        )
        sections[current_name].append(item)
        pending = None

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        heading = HEADING_RE.match(line)
        if heading:
            finalize()
            current_name = heading.group(1).strip()
            sections.setdefault(current_name, [])
            continue
        marker = MARKER_RE.match(line)
        if marker:
            finalize()
            if current_name is not None:
                pending = (line_number, marker.group(1).strip(), [])
            continue
        if line.startswith("【静默】") or line.startswith("表演："):
            finalize()
            continue
        if pending is not None and line and not line.startswith("#"):
            pending[2].append(line)
    finalize()
    return {name: items for name, items in sections.items() if items}
