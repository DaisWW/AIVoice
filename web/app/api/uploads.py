from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from ..audio_conversion import (
    AUDIO_FORMAT_LABEL,
    SUPPORTED_AUDIO_EXTENSIONS,
    AudioNormalizer,
)
from ..audio_quality import AudioQualityAnalyzer
from ..domain import ScriptItem
from ..script_parser import SUPPORTED_SCRIPT_EXTENSIONS, ScriptFormatError, parse_file
from ..services import ApplicationServices
from ..storage import save_upload


class ScriptStorage:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services

    def read(self, script_id: str) -> tuple[dict, list[ScriptItem]]:
        script = self._services.database.scripts.get(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="找不到台本")
        path = Path(str(script["source_path"]))
        if not path.is_file():
            raise HTTPException(status_code=409, detail="台本源文件已不在服务器上")
        return script, self._parse(path)

    async def store(
        self, upload: UploadFile, client_id: str
    ) -> tuple[str, list[ScriptItem]]:
        self._validate_extension(upload.filename or "")
        try:
            path, original_name, _ = await save_upload(
                upload,
                self._services.settings.script_upload_root / client_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            items = self._parse(path)
            script_id = self._services.database.scripts.create(
                Path(original_name).stem,
                original_name,
                path,
                client_id,
                "upload",
                items,
            )
            return script_id, items
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_extension(filename: str) -> None:
        if Path(filename).suffix.lower() in SUPPORTED_SCRIPT_EXTENSIONS:
            return
        allowed = "、".join(sorted(SUPPORTED_SCRIPT_EXTENSIONS))
        raise HTTPException(status_code=422, detail=f"台本仅支持 {allowed}")

    @staticmethod
    def _parse(path: Path) -> list[ScriptItem]:
        try:
            return parse_file(path)
        except ScriptFormatError as error:
            raise HTTPException(status_code=422, detail=f"台本格式错误: {error}") from error


class VoiceFileStorage:
    def __init__(self, services: ApplicationServices) -> None:
        self._services = services
        self._normalizer = AudioNormalizer()

    async def store(self, voice_id: str, files: list[UploadFile]) -> None:
        self._validate_files(files)
        saved_paths: list[Path] = []
        rows: list[tuple[str, Path, int, dict]] = []
        try:
            for upload in files:
                source, original_name, _ = await save_upload(
                    upload,
                    self._services.settings.voice_upload_root / voice_id,
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
