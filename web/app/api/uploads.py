from __future__ import annotations

import csv
import io
import uuid
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


class ScriptStorage:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services

    def load_items(self, script: dict[str, Any]) -> list[ScriptItem]:
        path = ensure_within(
            Path(str(script["source_path"])), self._services.settings.root
        )
        if not path.is_file():
            raise HTTPException(status_code=409, detail="台本源文件已不在服务器上")
        return self._parse(path)

    async def store(
        self,
        upload: UploadFile,
        owner_id: str,
        project_id: str,
        default_voice_id: str | None = None,
    ) -> tuple[str, list[ScriptItem]]:
        self._validate_extension(upload.filename or "")
        try:
            path, original_name, _ = await save_upload(
                upload,
                self._services.settings.script_upload_root / project_id,
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
            path.unlink(missing_ok=True)
            raise

    async def replace_from_upload(
        self,
        script: dict[str, Any],
        upload: UploadFile,
    ) -> list[ScriptItem]:
        self._validate_extension(upload.filename or "")
        try:
            content = await read_upload(upload)
            items = await run_in_threadpool(
                self._parse_content,
                content,
                Path(upload.filename or "").suffix,
            )
            self._validate_item_count(items)
            self.save_items(
                script,
                items,
                original_name=safe_filename(
                    upload.filename or str(script["original_name"])
                ),
            )
            return items
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
    ) -> None:
        old_path = ensure_within(
            Path(str(script["source_path"])), self._services.settings.root
        )
        with self._services.script_write_lock:
            old_path.parent.mkdir(parents=True, exist_ok=True)
            target = old_path.parent / f"{script['id']}.edited.csv"
            operation_id = uuid.uuid4().hex
            temporary = target.with_name(f".{target.name}.{operation_id}.tmp")
            backup = target.with_name(f".{target.name}.{operation_id}.bak")
            try:
                self._write_csv(temporary, items)
                if target.exists():
                    target.replace(backup)
                temporary.replace(target)
                updated = self._services.database.scripts.update_source(
                    str(script["id"]),
                    target,
                    len(items),
                    original_name=original_name,
                )
                if not updated:
                    raise HTTPException(status_code=404, detail="找不到台本")
            except Exception:
                temporary.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                if backup.exists():
                    backup.replace(target)
                raise
            backup.unlink(missing_ok=True)
            if old_path != target:
                old_path.unlink(missing_ok=True)

    @staticmethod
    def export_csv(items: list[ScriptItem]) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
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
    def _write_csv(path: Path, items: list[ScriptItem]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("text", "pronunciation"))
            writer.writerows((item.text, item.pronunciation) for item in items)


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
        try:
            for upload in files:
                source, original_name, _ = await save_upload(
                    upload,
                    self._services.settings.voice_upload_root / project_id / voice_id,
                )
                saved_paths.append(source)
                normalized = await run_in_threadpool(
                    self._normalizer.normalize,
                    source,
                )
                saved_paths.append(normalized)
                quality = await run_in_threadpool(
                    AudioQualityAnalyzer().analyze,
                    normalized,
                )
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
    def _validate_files(files: list[UploadFile]) -> None:
        if not files:
            raise HTTPException(status_code=422, detail="请至少上传一条真人录音")
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
            path.unlink(missing_ok=True)
