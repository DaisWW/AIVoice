from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Any

from ...domain import ScriptItem
from ..common import utc_now
from ..connection import SQLiteConnection


class JobRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def create(
        self,
        client_id: str,
        script_id: str,
        voice_id: str,
        model_id: str,
        items: list[ScriptItem],
        candidate_count: int = 2,
        reference_emotion: str = "all",
    ) -> str:
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        timestamp = utc_now()
        item_rows = [self._item_row(job_id, item) for item in items]
        with self._database.write() as connection:
            self._insert_job(
                connection,
                job_id,
                client_id,
                script_id,
                voice_id,
                model_id,
                candidate_count,
                reference_emotion,
                len(items),
                timestamp,
            )
            connection.executemany(
                """
                INSERT INTO job_items(
                    id, job_id, sequence, source_line, text, pronunciation, generated_text,
                    direction, emphasis, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
                """,
                item_rows,
            )
            connection.executemany(
                """
                INSERT INTO job_item_candidates(
                    id, job_item_id, source_candidate_id, origin_type, origin_id,
                    kind, name, mode, ordinal, seed, text, pronunciation,
                    generated_text, direction, emphasis, settings_json,
                    generation_settings_json, status, submitted_at
                ) VALUES (?, ?, NULL, ?, ?, 'gpt', ?, 'initial', ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'queued', ?)
                """,
                self._candidate_rows(
                    item_rows,
                    candidate_count,
                    timestamp,
                ),
            )
        return job_id

    @staticmethod
    def _insert_job(
        connection: Any,
        job_id: str,
        client_id: str,
        script_id: str,
        voice_id: str,
        model_id: str,
        candidate_count: int,
        reference_emotion: str,
        item_count: int,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO jobs(
                id, client_id, script_id, voice_id, model_id, effect_id,
                effect_settings_json, candidate_count, reference_emotion, status,
                total_items, completed_items, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, 0, ?)
            """,
            (
                job_id,
                client_id,
                script_id,
                voice_id,
                model_id,
                "clone_only",
                "{}",
                candidate_count,
                reference_emotion,
                item_count,
                timestamp,
            ),
        )

    @staticmethod
    def _item_row(job_id: str, item: ScriptItem) -> tuple[Any, ...]:
        return (
            f"item-{uuid.uuid4().hex[:14]}",
            job_id,
            item.order,
            item.source_line,
            item.text,
            item.pronunciation,
            item.generated_text,
            item.direction,
            ",".join(item.emphasis),
        )

    @staticmethod
    def _candidate_rows(
        item_rows: list[tuple[Any, ...]],
        candidate_count: int,
        timestamp: str,
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item_row in item_rows:
            item_id = str(item_row[0])
            for ordinal in range(1, candidate_count + 1):
                candidate_id = f"candidate-{uuid.uuid4().hex[:14]}"
                origin_type = "job_item" if ordinal == 1 else "generation"
                origin_id = item_id if ordinal == 1 else candidate_id
                rows.append(
                    (
                        candidate_id,
                        item_id,
                        origin_type,
                        origin_id,
                        f"候选 {ordinal}",
                        ordinal,
                        secrets.randbelow(2_147_483_647) + 1,
                        item_row[4],
                        item_row[5],
                        item_row[6],
                        item_row[7],
                        item_row[8],
                        "{}",
                        timestamp,
                    )
                )
        return rows

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._list_where("", (), limit)

    def list_for_client(self, client_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._list_where("WHERE j.client_id=?", (client_id,), limit)

    def _list_where(
        self, where: str, parameters: tuple[Any, ...], limit: int
    ) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT j.*, s.name AS script_name, v.name AS voice_name
                FROM jobs j
                JOIN scripts s ON s.id=j.script_id
                JOIN voices v ON v.id=j.voice_id
                {where}
                ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                         j.submitted_at DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def queued_ids(self) -> list[str]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY submitted_at, id"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def recover_interrupted(self) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='queued', started_at=NULL,
                    error='服务器重启后重新排队' WHERE status='running'
                """
            )
            connection.execute(
                "UPDATE job_items SET status='queued' WHERE status='running'"
            )
        return int(cursor.rowcount)

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._get_where("j.id=?", (job_id,))

    def get_for_client(self, job_id: str, client_id: str) -> dict[str, Any] | None:
        return self._get_where("j.id=? AND j.client_id=?", (job_id, client_id))

    def _get_where(
        self, where: str, parameters: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                f"""
                SELECT j.*, s.name AS script_name, s.source_path AS script_path,
                       v.name AS voice_name
                FROM jobs j
                JOIN scripts s ON s.id=j.script_id
                JOIN voices v ON v.id=j.voice_id
                WHERE {where}
                """,
                parameters,
            ).fetchone()
        return dict(row) if row else None

    def rename(
        self, job_id: str, display_name: str, client_id: str | None = None
    ) -> bool:
        query = "UPDATE jobs SET display_name=? WHERE id=?"
        parameters: tuple[str, ...] = (display_name.strip(), job_id)
        if client_id is not None:
            query += " AND client_id=?"
            parameters += (client_id,)
        with self._database.write() as connection:
            cursor = connection.execute(query, parameters)
        return cursor.rowcount == 1

    def items(self, job_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM job_items WHERE job_id=? ORDER BY sequence",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def item(self, job_id: str, item_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT * FROM job_items WHERE job_id=? AND id=?",
                (job_id, item_id),
            ).fetchone()
        return dict(row) if row else None

    def set_running(self, job_id: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE jobs SET status='running', started_at=?, error=''
                WHERE id=? AND status='queued'
                """,
                (utc_now(), job_id),
            )

    def update_progress(
        self, job_id: str, completed_items: int, eta_seconds: int | None
    ) -> None:
        with self._database.write() as connection:
            connection.execute(
                "UPDATE jobs SET completed_items=?, eta_seconds=? WHERE id=?",
                (completed_items, eta_seconds, job_id),
            )

    def finish(self, job_id: str, status: str, error: str = "") -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE jobs SET status=?, finished_at=?, eta_seconds=0, error=? WHERE id=?
                """,
                (status, utc_now(), error, job_id),
            )

    def requeue(self, job_id: str, message: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE jobs SET status='queued', started_at=NULL,
                    eta_seconds=NULL, error=? WHERE id=?
                """,
                (message, job_id),
            )
            connection.execute(
                """
                UPDATE job_items SET status='queued'
                WHERE job_id=? AND status='running'
                """,
                (job_id,),
            )

    def fail_pending_items(self, job_id: str, message: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE job_items SET status='failed', error=?
                WHERE job_id=? AND status IN ('queued', 'running')
                """,
                (message, job_id),
            )

    def set_item_running(self, item_id: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                "UPDATE job_items SET status='running', error='' WHERE id=?",
                (item_id,),
            )

    def finish_item(
        self,
        item_id: str,
        status: str,
        audio_path: Path | None = None,
        raw_audio_path: Path | None = None,
        duration_seconds: float | None = None,
        elapsed_seconds: float | None = None,
        processing_backend: str = "",
        error: str = "",
    ) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE job_items SET status=?, audio_path=?, raw_audio_path=?, duration_seconds=?,
                    elapsed_seconds=?, processing_backend=?, error=? WHERE id=?
                """,
                (
                    status,
                    str(audio_path or ""),
                    str(raw_audio_path or ""),
                    duration_seconds,
                    elapsed_seconds,
                    processing_backend,
                    error,
                    item_id,
                ),
            )
