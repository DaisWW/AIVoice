from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...domain import ScriptItem
from ..common import utc_now
from ..connection import SQLiteConnection


@dataclass(frozen=True)
class LegacyJob:
    id: str
    script_id: str
    voice_id: str
    effect_id: str
    items: list[dict[str, Any]]


class LegacyRepository:
    """Persistence operations used only by the idempotent legacy importer."""

    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def remap_effects(self) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE jobs SET effect_id=CASE effect_id
                    WHEN 'yugong' THEN 'steady_male'
                    WHEN 'chunzhijing' THEN 'loli'
                    WHEN 'yanhua' THEN 'warm'
                    WHEN 'dazuo' THEN 'ethereal'
                    WHEN 'yuweng' THEN 'elder'
                    ELSE effect_id END
                WHERE client_id='legacy'
                """
            )

    def add_voice_if_missing(self, voice_id: str) -> bool:
        with self._database.write() as connection:
            if self._exists(connection, "voices", voice_id):
                return False
            connection.execute(
                """
                INSERT INTO voices(id, name, owner_id, source_kind, notes, created_at)
                VALUES (?, ?, 'legacy', 'legacy', ?, ?)
                """,
                (voice_id, voice_id, "从旧 input 自动导入", utc_now()),
            )
        return True

    def add_voice_file_if_missing(
        self,
        file_id: str,
        voice_id: str,
        audio_path: Path,
        enabled: bool,
    ) -> bool:
        with self._database.write() as connection:
            if self._exists(connection, "voice_files", file_id):
                return False
            connection.execute(
                """
                INSERT INTO voice_files(
                    id, voice_id, original_name, source_path, size_bytes, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    voice_id,
                    audio_path.name,
                    str(audio_path),
                    audio_path.stat().st_size,
                    int(enabled),
                    utc_now(),
                ),
            )
        return True

    def upsert_script(
        self,
        script_id: str,
        source_path: Path,
        original_name: str,
        default_voice_id: str | None,
        default_effect_id: str | None,
        item_count: int,
    ) -> bool:
        with self._database.write() as connection:
            if self._exists(connection, "scripts", script_id):
                self._update_script(
                    connection,
                    script_id,
                    source_path,
                    default_voice_id,
                    default_effect_id,
                    item_count,
                )
                return False
            self._insert_script(
                connection,
                script_id,
                source_path,
                original_name,
                default_voice_id,
                default_effect_id,
                item_count,
            )
        return True

    @staticmethod
    def _update_script(
        connection: Any,
        script_id: str,
        source_path: Path,
        default_voice_id: str | None,
        default_effect_id: str | None,
        item_count: int,
    ) -> None:
        connection.execute(
            """
            UPDATE scripts SET source_path=?, default_voice_id=?,
                default_effect_id=?, item_count=? WHERE id=?
            """,
            (
                str(source_path),
                default_voice_id,
                default_effect_id,
                item_count,
                script_id,
            ),
        )

    @staticmethod
    def _insert_script(
        connection: Any,
        script_id: str,
        source_path: Path,
        original_name: str,
        default_voice_id: str | None,
        default_effect_id: str | None,
        item_count: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO scripts(
                id, name, original_name, source_path, owner_id, source_kind,
                default_voice_id, default_effect_id, item_count, created_at
            ) VALUES (?, ?, ?, ?, 'legacy', 'legacy', ?, ?, ?, ?)
            """,
            (
                script_id,
                Path(original_name).stem,
                original_name,
                str(source_path),
                default_voice_id,
                default_effect_id,
                item_count,
                utc_now(),
            ),
        )

    def add_job_if_missing(self, job: LegacyJob) -> bool:
        with self._database.write() as connection:
            if self._exists(connection, "jobs", job.id):
                return False
            if not self._exists(connection, "scripts", job.script_id):
                return False
            if not self._exists(connection, "voices", job.voice_id):
                return False
            self._insert_job(connection, job)
            self._insert_job_items(connection, job)
        return True

    @staticmethod
    def _insert_job(connection: Any, job: LegacyJob) -> None:
        timestamp = utc_now()
        connection.execute(
            """
            INSERT INTO jobs(
                id, client_id, script_id, voice_id, model_id, effect_id, status,
                total_items, completed_items, submitted_at, started_at, finished_at,
                eta_seconds
            ) VALUES (?, 'legacy', ?, ?, 'legacy-gpt-sovits-v2', ?, 'completed',
                ?, ?, ?, ?, ?, 0)
            """,
            (
                job.id,
                job.script_id,
                job.voice_id,
                job.effect_id,
                len(job.items),
                len(job.items),
                timestamp,
                timestamp,
                timestamp,
            ),
        )

    @staticmethod
    def _insert_job_items(connection: Any, job: LegacyJob) -> None:
        rows = [LegacyRepository._job_item_row(job.id, item) for item in job.items]
        connection.executemany(
            """
            INSERT INTO job_items(
                id, job_id, sequence, source_line, text, pronunciation, generated_text,
                direction, emphasis, status, audio_path, raw_audio_path,
                processing_backend
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            rows,
        )
        LegacyRepository._insert_initial_candidates(connection, rows)

    @staticmethod
    def _insert_initial_candidates(
        connection: Any, item_rows: list[tuple[Any, ...]]
    ) -> None:
        timestamp = utc_now()
        connection.executemany(
            """
            INSERT INTO job_item_candidates(
                id, job_item_id, origin_type, origin_id, kind, name, mode,
                ordinal, text, pronunciation, generated_text, direction,
                emphasis, settings_json, generation_settings_json, status,
                raw_audio_path, audio_path, processing_backend, submitted_at,
                started_at, finished_at
            ) VALUES (?, ?, 'job_item', ?, 'gpt', '初始候选', 'initial', 1,
                ?, ?, ?, ?, ?, '{}', '{}', 'completed', ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"candidate-initial-{row[0]}",
                    row[0],
                    row[0],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[10],
                    row[9],
                    row[11],
                    timestamp,
                    timestamp,
                    timestamp,
                )
                for row in item_rows
            ],
        )
        connection.executemany(
            "UPDATE job_items SET accepted_candidate_id=? WHERE id=?",
            [(f"candidate-initial-{row[0]}", row[0]) for row in item_rows],
        )

    @staticmethod
    def _job_item_row(job_id: str, item: dict[str, Any]) -> tuple[Any, ...]:
        generated_text = str(item.get("generated_text") or "")
        return (
            f"item-{uuid.uuid4().hex[:14]}",
            job_id,
            int(item.get("audio_order") or 0),
            int(item.get("source_line") or 0),
            generated_text,
            str(item.get("pronunciation") or ""),
            generated_text,
            str(item.get("direction") or "flat"),
            str(item.get("emphasis") or ""),
            str(Path(str(item.get("final_audio"))).resolve()),
            str(item.get("raw_audio") or ""),
            str(item.get("processing_backend") or "legacy"),
        )

    def backfill_job_items(
        self, script_name: str, display_items: list[ScriptItem]
    ) -> None:
        with self._database.write() as connection:
            script = connection.execute(
                """
                SELECT id FROM scripts
                WHERE name=? AND source_kind='legacy' LIMIT 1
                """,
                (script_name,),
            ).fetchone()
            if not script:
                return
            updates = [
                (
                    item.text,
                    item.pronunciation,
                    item.generated_text,
                    item.direction,
                    ",".join(item.emphasis),
                    script["id"],
                    item.order,
                )
                for item in display_items
            ]
            connection.executemany(
                """
                UPDATE job_items SET text=?, pronunciation=?, generated_text=?,
                    direction=?, emphasis=?
                WHERE job_id IN (
                    SELECT id FROM jobs WHERE script_id=? AND client_id='legacy'
                ) AND sequence=?
                """,
                updates,
            )
            connection.executemany(
                """
                UPDATE job_item_candidates
                SET text=?, pronunciation=?, generated_text=?, direction=?, emphasis=?
                WHERE job_item_id IN (
                    SELECT ji.id FROM job_items ji JOIN jobs j ON j.id=ji.job_id
                    WHERE j.script_id=? AND j.client_id='legacy' AND ji.sequence=?
                ) AND origin_type='job_item'
                """,
                updates,
            )

    @staticmethod
    def _exists(connection: Any, table: str, record_id: str) -> bool:
        return bool(
            connection.execute(
                f"SELECT 1 FROM {table} WHERE id=?", (record_id,)
            ).fetchone()
        )
