from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


class VoiceRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def create(
        self,
        name: str,
        owner_id: str,
        notes: str,
        source_kind: str = "upload",
        voice_id: str | None = None,
        project_id: str = "",
    ) -> str:
        voice_id = voice_id or f"voice-{uuid.uuid4().hex[:12]}"
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO voices(
                    id, name, owner_id, project_id, source_kind, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    voice_id,
                    name.strip(),
                    owner_id,
                    project_id,
                    source_kind,
                    notes.strip(),
                    utc_now(),
                ),
            )
        return voice_id

    def add_file(
        self,
        voice_id: str,
        original_name: str,
        source_path: Path,
        size_bytes: int,
        enabled: bool = True,
        file_id: str | None = None,
        emotion_tag: str = "neutral",
        reference_text: str = "",
        quality: dict[str, Any] | None = None,
    ) -> str:
        file_id = file_id or f"voice-file-{uuid.uuid4().hex[:12]}"
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO voice_files(
                    id, voice_id, original_name, source_path, size_bytes,
                    enabled, emotion_tag, reference_text, quality_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    voice_id,
                    original_name,
                    str(source_path),
                    size_bytes,
                    int(enabled),
                    emotion_tag,
                    reference_text.strip(),
                    json.dumps(
                        quality or {}, ensure_ascii=False, separators=(",", ":")
                    ),
                    utc_now(),
                ),
            )
        return file_id

    def add_files(
        self,
        voice_id: str,
        files: Sequence[tuple[str, Path, int, dict[str, Any]]],
    ) -> list[str]:
        file_ids = [f"voice-file-{uuid.uuid4().hex[:12]}" for _ in files]
        timestamp = utc_now()
        rows = [
            (
                file_id,
                voice_id,
                name,
                str(path),
                size,
                json.dumps(quality, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            )
            for file_id, (name, path, size, quality) in zip(
                file_ids, files, strict=True
            )
        ]
        with self._database.write() as connection:
            connection.executemany(
                """
                INSERT INTO voice_files(
                    id, voice_id, original_name, source_path, size_bytes,
                    enabled, emotion_tag, quality_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, 'neutral', ?, ?)
                """,
                rows,
            )
        return file_ids

    def delete_empty(self, voice_id: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                DELETE FROM voices WHERE id=?
                AND NOT EXISTS (SELECT 1 FROM voice_files WHERE voice_id=?)
                AND NOT EXISTS (SELECT 1 FROM jobs WHERE voice_id=? )
                """,
                (voice_id, voice_id, voice_id),
            )

    def get(self, voice_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT v.*, COUNT(vf.id) AS file_count,
                       SUM(CASE WHEN vf.enabled=1 THEN 1 ELSE 0 END) AS enabled_file_count,
                       COALESCE(SUM(vf.size_bytes), 0) AS size_bytes
                FROM voices v LEFT JOIN voice_files vf ON vf.voice_id=v.id
                WHERE v.id=? GROUP BY v.id
                """,
                (voice_id,),
            ).fetchone()
        return dict(row) if row else None

    def update(self, voice_id: str, name: str, notes: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                "UPDATE voices SET name=?, notes=? WHERE id=?",
                (name.strip(), notes.strip(), voice_id),
            )
        return cursor.rowcount == 1

    def list(self, project_id: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE v.project_id=?" if project_id is not None else ""
        parameters = (project_id,) if project_id is not None else ()
        with self._database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT v.*, COUNT(vf.id) AS file_count,
                       SUM(CASE WHEN vf.enabled=1 THEN 1 ELSE 0 END) AS enabled_file_count,
                       COALESCE(SUM(vf.size_bytes), 0) AS size_bytes
                FROM voices v LEFT JOIN voice_files vf ON vf.voice_id=v.id
                {where}
                GROUP BY v.id ORDER BY v.created_at DESC
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_files(
        self, voice_id: str, enabled_only: bool = True
    ) -> list[dict[str, Any]]:
        enabled_filter = " AND enabled=1" if enabled_only else ""
        with self._database.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM voice_files WHERE voice_id=?{enabled_filter} ORDER BY created_at, original_name",
                (voice_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT vf.*, v.name AS voice_name
                FROM voice_files vf JOIN voices v ON v.id=vf.voice_id
                WHERE vf.id=?
                """,
                (file_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_file(
        self,
        file_id: str,
        *,
        enabled: bool | None = None,
        emotion_tag: str | None = None,
        reference_text: str | None = None,
    ) -> bool:
        """Apply all recording metadata changes in one transaction."""
        with self._database.write() as connection:
            file_row = connection.execute(
                "SELECT voice_id, enabled FROM voice_files WHERE id=?", (file_id,)
            ).fetchone()
            if not file_row:
                return False
            if enabled is False and bool(file_row["enabled"]):
                self._ensure_another_enabled_file(connection, str(file_row["voice_id"]))
            connection.execute(
                """
                UPDATE voice_files
                SET enabled=COALESCE(?, enabled),
                    emotion_tag=COALESCE(?, emotion_tag),
                    reference_text=COALESCE(?, reference_text)
                WHERE id=?
                """,
                (
                    int(enabled) if enabled is not None else None,
                    emotion_tag,
                    reference_text,
                    file_id,
                ),
            )
        return True

    def update_file_quality(self, file_id: str, quality: dict[str, Any]) -> None:
        with self._database.write() as connection:
            connection.execute(
                "UPDATE voice_files SET quality_json=? WHERE id=?",
                (
                    json.dumps(quality, ensure_ascii=False, separators=(",", ":")),
                    file_id,
                ),
            )

    @staticmethod
    def _ensure_another_enabled_file(connection: Any, voice_id: str) -> None:
        enabled_count = connection.execute(
            "SELECT COUNT(*) FROM voice_files WHERE voice_id=? AND enabled=1",
            (voice_id,),
        ).fetchone()[0]
        if int(enabled_count) <= 1:
            raise ValueError("声音库必须保留至少一条启用的录音")

    def list_all_files(self) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT vf.*, v.name AS voice_name
                FROM voice_files vf JOIN voices v ON v.id=vf.voice_id
                ORDER BY v.name, vf.created_at, vf.original_name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_file_record(self, file_id: str) -> None:
        with self._database.write() as connection:
            connection.execute("DELETE FROM voice_files WHERE id=?", (file_id,))
