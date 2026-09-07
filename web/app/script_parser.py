from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
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
MAX_DOCX_MEMBERS = 4096
MAX_DOCX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 1000


class ScriptFormatError(ValueError):
    pass


def analyze_script_pronunciation(pronunciation: str) -> PronunciationAnalysis:
    try:
        return analyze_pronunciation(pronunciation)
    except PronunciationError as error:
        raise ScriptFormatError(str(error)) from error


def build_script_item(
    text: str,
    pronunciation: str,
    line_number: int,
    order: int,
    rewrite_instruction: str = "",
) -> ScriptItem:
    """Validate one editable row and derive its generation metadata."""
    pronunciation = pronunciation.strip()
    text = text.strip()
    rewrite_instruction = str(rewrite_instruction or "").strip()
    if len(rewrite_instruction) > 4000:
        raise ScriptFormatError(f"第 {line_number} 行修改要求不能超过 4000 个字符")
    if not pronunciation:
        pronunciation = text
    if not pronunciation:
        raise ScriptFormatError(f"第 {line_number} 行缺少发音标记")
    analysis = analyze_script_pronunciation(pronunciation)
    return ScriptItem(
        order=order,
        source_line=line_number,
        text=text or analysis.generated_text,
        pronunciation=pronunciation,
        generated_text=analysis.generated_text,
        direction=analysis.direction,
        emphasis=analysis.emphasis,
        hold_units=analysis.hold_units,
        raw_mode=analysis.raw_mode,
        rewrite_instruction=rewrite_instruction,
    )


_item = build_script_item


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
    rewrite_instruction_key = next(
        (
            reader.fieldnames[i]
            for i, name in enumerate(fieldnames)
            if name
            in {
                "rewrite_instruction",
                "rewrite_instruction_text",
                "修改要求",
                "单行修改要求",
                "ai 单行修改要求",
                "ai单行修改要求",
            }
        ),
        None,
    )
    if pronunciation_key is None:
        raise ScriptFormatError("CSV 必须包含 pronunciation（或 发音）列")
    result: list[ScriptItem] = []
    for line_number, row in enumerate(reader, start=2):
        pronunciation = str(row.get(pronunciation_key) or "").strip()
        text = str(row.get(text_key) or "").strip() if text_key else ""
        rewrite_instruction = (
            str(row.get(rewrite_instruction_key) or "").strip()
            if rewrite_instruction_key
            else ""
        )
        if pronunciation:
            result.append(
                _item(
                    text,
                    pronunciation,
                    line_number,
                    len(result) + 1,
                    rewrite_instruction,
                )
            )
    if not result:
        raise ScriptFormatError("CSV 没有可生成的发音行")
    return result


def parse_content(content: bytes, suffix: str) -> list[ScriptItem]:
    suffix = suffix.lower()
    if suffix not in SUPPORTED_SCRIPT_EXTENSIONS:
        raise ScriptFormatError("台本仅支持 .txt、.md、.csv、.docx")
    if suffix == ".docx":
        sections = parse_docx_sections(content)
        items = [item for rows in sections.values() for item in rows]
        if not items:
            raise ScriptFormatError("DOCX 没有可生成的非空台词")
        return [
            build_script_item(
                item.text,
                item.pronunciation,
                item.source_line,
                order,
            )
            for order, item in enumerate(items, start=1)
        ]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ScriptFormatError("文本台本必须使用 UTF-8 编码") from error
    return _parse_csv(text) if suffix == ".csv" else _parse_rows(text.splitlines())


def parse_file(path: Path) -> list[ScriptItem]:
    return parse_content(path.read_bytes(), path.suffix)


@dataclass
class _DocxSectionParser:
    sections: OrderedDict[str, list[ScriptItem]] = field(default_factory=OrderedDict)
    paragraph_notes: dict[str, list[str]] = field(default_factory=dict)
    no_dialogue_roles: set[str] = field(default_factory=set)
    current_role: str | None = None
    blank_paragraphs: int = 0
    source_line: int = 0
    previous_was_table: bool = False

    def handle_paragraph(self, text: str) -> None:
        if not text:
            self.blank_paragraphs += 1
            return
        self.source_line += 1
        is_heading = (
            self.current_role is None
            or self.blank_paragraphs >= 2
            or (self.previous_was_table and self.blank_paragraphs >= 1)
        )
        if is_heading:
            self.current_role = text
            self.sections.setdefault(text, [])
            self.paragraph_notes.setdefault(text, [])
        else:
            self.paragraph_notes.setdefault(self.current_role, []).append(text)
        self.blank_paragraphs = 0
        self.previous_was_table = False

    def handle_table(self, table: object) -> None:
        if self.current_role is None:
            return
        rows = self.sections.setdefault(self.current_role, [])
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            text = next((value for value in cells if value), "")
            if not text:
                continue
            self.source_line += 1
            rows.append(build_script_item(text, text, self.source_line, len(rows) + 1))
        self.blank_paragraphs = 0
        self.previous_was_table = True

    def finalize(self) -> dict[str, list[ScriptItem]]:
        for role, notes in self.paragraph_notes.items():
            if any(
                line.strip().startswith("无台词")
                for note in notes
                for line in note.splitlines()
            ):
                self.no_dialogue_roles.add(role)

        for role, notes in self.paragraph_notes.items():
            rows = self.sections.setdefault(role, [])
            for note in notes:
                for line in note.splitlines():
                    text = line.strip()
                    if not text:
                        continue
                    if text.startswith("无台词"):
                        text = text[len("无台词") :].strip()
                        if not text:
                            continue
                    elif text.startswith("（") and role not in self.no_dialogue_roles:
                        continue
                    if role in self.no_dialogue_roles:
                        text = text.replace("（", "").replace("）", "")
                    elif text.endswith("）"):
                        text = text[:-1].rstrip()
                    if not text:
                        continue
                    self.source_line += 1
                    rows.append(
                        build_script_item(text, text, self.source_line, len(rows) + 1)
                    )

        # Keep explicitly marked no-dialogue roles, including their stage-direction
        # lines, so callers can preserve the complete cast list.
        return {
            role: rows
            for role, rows in self.sections.items()
            if rows or role in self.no_dialogue_roles
        }


def parse_docx_sections(content: bytes) -> dict[str, list[ScriptItem]]:
    """Group one-column DOCX tables by the nearest role heading.

    The source document uses blank paragraphs as section separators and can
    continue one role across several adjacent tables. Rows without text are
    intentionally skipped; pronunciation starts as the source text and can be
    refined later in the script editor. Stage directions following an explicit
    ``无台词`` marker are retained as generation lines for that role.
    """
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as error:  # pragma: no cover
        raise ScriptFormatError("当前环境未安装 python-docx") from error
    validate_docx_archive(content)
    try:
        document = Document(io.BytesIO(content))
    except Exception as error:
        raise ScriptFormatError("DOCX 文件损坏或不是有效的 Word 文档") from error

    parser = _DocxSectionParser()

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            parser.handle_paragraph(paragraph.text.strip())
            continue
        if not child.tag.endswith("}tbl"):
            continue
        table = Table(child, document)
        parser.handle_table(table)
    return parser.finalize()


def validate_docx_archive(content: bytes) -> None:
    """Reject oversized or suspicious ZIP structures before python-docx expands them."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise ScriptFormatError("DOCX 文件损坏或不是有效的 Word 文档") from error

    if len(members) > MAX_DOCX_MEMBERS:
        raise ScriptFormatError("DOCX 文件包含过多压缩条目")
    names: set[str] = set()
    total_size = 0
    for member in members:
        name = str(member.filename).replace("\\", "/")
        if (
            not name
            or name in names
            or name.startswith("/")
            or any(part == ".." for part in name.split("/"))
        ):
            raise ScriptFormatError("DOCX 压缩包条目无效")
        names.add(name)
        if member.flag_bits & 0x1:
            raise ScriptFormatError("DOCX 加密文档不受支持")
        size = int(member.file_size)
        compressed = int(member.compress_size)
        if size < 0 or size > MAX_DOCX_MEMBER_BYTES:
            raise ScriptFormatError("DOCX 单个压缩条目过大")
        total_size += size
        if total_size > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise ScriptFormatError("DOCX 解压后内容过大")
        if size and (compressed <= 0 or size > compressed * MAX_DOCX_COMPRESSION_RATIO):
            raise ScriptFormatError("DOCX 压缩比异常")


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
