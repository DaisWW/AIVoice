from __future__ import annotations

import csv
import io
import math
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from ..audio_conversion import (
    AUDIO_FORMAT_LABEL,
    SUPPORTED_AUDIO_EXTENSIONS,
    AudioNormalizer,
)
from ..audio_quality import AudioQualityAnalyzer
from ..domain import MAX_SCRIPT_ITEMS, ScriptItem
from ..script_parser import (
    SUPPORTED_SCRIPT_EXTENSIONS,
    ScriptFormatError,
    parse_content,
    parse_file,
)
from ..services import ApplicationServices
from ..storage import ensure_within, read_upload, safe_filename, save_upload


MAX_VOICE_FILES = 32
MAX_VOICE_UPLOAD_BYTES = 1 * 1024 * 1024 * 1024
MAX_VOICE_DURATION_SECONDS = 30 * 60


def _upload_directory(root: Path, *parts: str) -> Path:
    try:
        return ensure_within(root.joinpath(*parts), root)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise HTTPException(status_code=409, detail="上传目录无效") from None


class ScriptStorage:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services

    def load_items(self, script: dict[str, Any]) -> list[ScriptItem]:
        try:
            path = ensure_within(
                Path(str(script["source_path"])), self._services.settings.root
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            raise HTTPException(status_code=409, detail="台本源文件路径无效") from None
        if not path.is_file():
            raise HTTPException(status_code=409, detail="台本源文件已不在服务器上")
        try:
            item_count = int(script.get("item_count") or 0)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(status_code=409, detail="台本记录损坏，无法读取") from None
        if item_count < 0:
            raise HTTPException(status_code=409, detail="台本记录损坏，无法读取")
        if item_count == 0:
            return []
        return self._parse(path)

    async def store(
        self,
        upload: UploadFile,
        owner_id: str,
        project_id: str,
        default_voice_id: str | None = None,
    ) -> tuple[str, list[ScriptItem]]:
        self._validate_extension(upload.filename or "")
        directory = _upload_directory(
            self._services.settings.script_upload_root, project_id
        )
        try:
            path, original_name, _ = await save_upload(
                upload,
                directory,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            name = self._script_name(original_name)
            items = await run_in_threadpool(self._parse, path)
            self._validate_item_count(items)
            script_id = self._services.database.scripts.create(
                name,
                original_name,
                path,
                owner_id,
                "upload",
                items,
                default_voice_id=default_voice_id,
                project_id=project_id,
            )
            return script_id, items
        except Exception:
            self._unlink_quietly(path)
            raise

    async def replace_from_upload(
        self,
        script: dict[str, Any],
        upload: UploadFile,
        *,
        expected_version: int | None = None,
    ) -> list[ScriptItem]:
        items, original_name = await self.prepare_upload(
            upload, fallback_name=str(script.get("original_name") or "台本.txt")
        )
        self.save_items(
            script,
            items,
            original_name=original_name,
            expected_version=expected_version,
        )
        return items

    async def prepare_upload(
        self, upload: UploadFile, *, fallback_name: str = "台本.txt"
    ) -> tuple[list[ScriptItem], str]:
        """Read and validate an upload before taking the script mutation lock."""
        self._validate_extension(upload.filename or "")
        try:
            content = await read_upload(upload)
            items = await run_in_threadpool(
                self._parse_content,
                content,
                Path(upload.filename or "").suffix,
            )
            self._validate_item_count(items)
            return items, safe_filename(upload.filename or fallback_name)
        except ScriptFormatError as error:
            raise HTTPException(status_code=422, detail=f"台本格式错误: {error}") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def save_items(
        self,
        script: dict[str, Any],
        items: list[ScriptItem],
        *,
        original_name: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        try:
            old_path = ensure_within(
                Path(str(script["source_path"])), self._services.settings.root
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            raise HTTPException(status_code=409, detail="台本源文件路径无效") from None
        with self._services.script_write_lock:
            old_path.parent.mkdir(parents=True, exist_ok=True)
            script_id = str(script.get("id") or "").strip()
            if not script_id:
                raise HTTPException(status_code=409, detail="台本记录损坏，无法保存")
            try:
                target = ensure_within(
                    old_path.parent / f"{script_id}.edited.csv",
                    self._services.settings.root,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                raise HTTPException(status_code=409, detail="台本保存路径无效") from None
            with atomic_csv_publish(target, items, write_csv=self._write_csv):
                update_kwargs: dict[str, Any] = {"original_name": original_name}
                if expected_version is not None:
                    update_kwargs["expected_version"] = expected_version
                updated = self._services.database.scripts.update_source(
                    script_id, target, len(items), **update_kwargs
                )
                if not updated:
                    status = 409 if expected_version is not None else 404
                    detail = (
                        "台本已被其他用户修改，请刷新后重试" if expected_version is not None else "找不到台本"
                    )
                    raise HTTPException(status_code=status, detail=detail)
            if old_path != target:
                self._unlink_quietly(old_path)

    @staticmethod
    def export_csv(items: list[ScriptItem]) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        include_rewrite_instruction = any(
            item.rewrite_instruction.strip() for item in items
        )
        if include_rewrite_instruction:
            writer.writerow(("text", "pronunciation", "rewrite_instruction"))
            writer.writerows(
                (
                    item.text,
                    item.pronunciation,
                    item.rewrite_instruction,
                )
                for item in items
            )
        else:
            writer.writerow(("text", "pronunciation"))
            writer.writerows((item.text, item.pronunciation) for item in items)
        return output.getvalue()

    @staticmethod
    def _validate_extension(filename: str) -> None:
        if Path(filename).suffix.lower() in SUPPORTED_SCRIPT_EXTENSIONS:
            return
        allowed = "、".join(sorted(SUPPORTED_SCRIPT_EXTENSIONS))
        raise HTTPException(status_code=422, detail=f"台本仅支持 {allowed}")

    @staticmethod
    def _script_name(filename: str) -> str:
        name = Path(filename).stem.strip()
        if not name or len(name) > 80:
            raise HTTPException(status_code=422, detail="台本名称需为 1-80 个字符")
        return name

    @staticmethod
    def _parse(path: Path) -> list[ScriptItem]:
        try:
            return parse_file(path)
        except ScriptFormatError as error:
            raise HTTPException(status_code=422, detail=f"台本格式错误: {error}") from error
        except OSError as error:
            raise HTTPException(status_code=409, detail="台本文件暂时无法读取") from error

    @staticmethod
    def _parse_content(content: bytes, suffix: str) -> list[ScriptItem]:
        return parse_content(content, suffix)

    @staticmethod
    def _validate_item_count(items: list[ScriptItem]) -> None:
        if len(items) > MAX_SCRIPT_ITEMS:
            raise HTTPException(
                status_code=422,
                detail=f"台本最多支持 {MAX_SCRIPT_ITEMS} 条台词",
            )

    @staticmethod
    def _write_csv(path: Path, items: Sequence[ScriptItem]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            include_rewrite_instruction = any(
                item.rewrite_instruction.strip() for item in items
            )
            if include_rewrite_instruction:
                writer.writerow(("text", "pronunciation", "rewrite_instruction"))
                writer.writerows(
                    (
                        item.text,
                        item.pronunciation,
                        item.rewrite_instruction,
                    )
                    for item in items
                )
            else:
                writer.writerow(("text", "pronunciation"))
                writer.writerows((item.text, item.pronunciation) for item in items)

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return


@contextmanager
def atomic_csv_publish(
    target: Path,
    items: Sequence[ScriptItem],
    *,
    write_csv: Callable[[Path, Sequence[ScriptItem]], None],
) -> Iterator[None]:
    """Publish a CSV while restoring the previous file if persistence fails."""
    operation_id = uuid.uuid4().hex
    temporary = target.with_name(f".{target.name}.{operation_id}.tmp")
    backup = target.with_name(f".{target.name}.{operation_id}.bak")
    published = False
    try:
        write_csv(temporary, items)
        if target.exists() or target.is_symlink():
            target.replace(backup)
        temporary.replace(target)
        published = True
        yield
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if published:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        if backup.exists():
            try:
                backup.replace(target)
            except OSError:
                pass
        raise
    try:
        backup.unlink(missing_ok=True)
    except OSError:
        pass


class VoiceFileStorage:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services
        self._normalizer = AudioNormalizer()

    async def store(
        self, voice_id: str, files: list[UploadFile], project_id: str = ""
    ) -> None:
        self._validate_files(files)
        saved_paths: list[Path] = []
        rows: list[tuple[str, Path, int, dict]] = []
        total_bytes = 0
        total_duration = 0.0
        directory = _upload_directory(
            self._services.settings.voice_upload_root, project_id, voice_id
        )
        try:
            for upload in files:
                source, original_name, _ = await save_upload(
                    upload,
                    directory,
                )
                source = ensure_within(source, directory)
                saved_paths.append(source)
                total_bytes += source.stat().st_size
                if total_bytes > MAX_VOICE_UPLOAD_BYTES:
                    raise ValueError("本次录音上传总大小不能超过 1 GB")
                normalized_result = await run_in_threadpool(
                    self._normalizer.normalize,
                    source,
                )
                normalized = ensure_within(normalized_result, directory)
                saved_paths.append(normalized)
                quality = await run_in_threadpool(
                    AudioQualityAnalyzer().analyze,
                    normalized,
                )
                total_duration += self._quality_duration(quality)
                if total_duration > MAX_VOICE_DURATION_SECONDS:
                    raise ValueError("本次录音总时长不能超过 30 分钟")
                rows.append(
                    (
                        original_name,
                        normalized,
                        normalized.stat().st_size,
                        quality,
                    )
                )
            self._services.database.voices.add_files(voice_id, rows)
        except ValueError as error:
            self._remove(saved_paths)
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception:
            self._remove(saved_paths)
            raise

    @staticmethod
    def _quality_duration(quality: Any) -> float:
        if not isinstance(quality, dict) or quality.get("duration_seconds") is None:
            raise ValueError("音频质量检测结果无效")
        try:
            duration = float(quality["duration_seconds"])
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("音频质量检测结果无效") from error
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("音频质量检测结果无效")
        return duration

    @staticmethod
    def _validate_files(files: list[UploadFile]) -> None:
        if not files:
            raise HTTPException(status_code=422, detail="请至少上传一条真人录音")
        if len(files) > MAX_VOICE_FILES:
            raise HTTPException(
                status_code=422,
                detail=f"一次最多上传 {MAX_VOICE_FILES} 条真人录音",
            )
        invalid = [
            upload.filename or ""
            for upload in files
            if Path(upload.filename or "").suffix.lower()
            not in SUPPORTED_AUDIO_EXTENSIONS
        ]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"声音库支持 {AUDIO_FORMAT_LABEL}: " + "、".join(invalid),
            )

    @staticmethod
    def _remove(paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue


# Compatibility exports for callers that imported smart import symbols here.
from .smart_script_import import (  # noqa: E402,F401
    MAX_SMART_IMPORT_ANALYSIS_CHARACTERS,
    MAX_SMART_IMPORT_ANALYSIS_UNITS,
    MAX_SMART_IMPORT_CHARACTERS,
    MAX_SMART_IMPORT_DRAFTS,
    MAX_SMART_IMPORT_FILES,
    MAX_SMART_IMPORT_UNITS,
    MAX_SMART_IMPORT_UPLOAD_BYTES,
    SmartImportSource,
    SmartScriptImport,
    SmartScriptImportError,
    _extract_smart_import_units,
    _smart_import_analysis_chunks,
    _validate_smart_import_segments,
)
