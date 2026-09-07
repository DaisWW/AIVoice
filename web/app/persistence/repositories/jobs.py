from __future__ import annotations

import json
import secrets
import uuid
from pathlib import Path
from typing import Any

from ...domain import MAX_CANDIDATES_PER_ITEM, ScriptItem
from ..common import utc_now
from ..connection import SQLiteConnection


MAX_SEED = 2_147_483_647


def _candidate_seed(
    base_seed: int | None,
    item_index: int,
    ordinal: int,
    seed_stride: int,
) -> int:
    if base_seed is None:
        return secrets.randbelow(2_147_483_647) + 1
    return base_seed + item_index * seed_stride + ordinal - 1


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
        generation_settings: dict[str, float | int] | None = None,
        base_seed: int | None = None,
        seed_stride: int | None = None,
        project_id: str = "",
        created_by: str = "",
    ) -> str:
        self._validate_create_arguments(items, candidate_count, base_seed, seed_stride)
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        timestamp = utc_now()
        item_rows = [self._item_row(job_id, item) for item in items]
        if generation_settings is not None and not isinstance(
            generation_settings, dict
        ):
            raise ValueError("生成参数必须是对象")
        try:
            settings_json = json.dumps(generation_settings or {}, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("生成参数格式无效") from error
        with self._database.write() as connection:
            reference_files = self._reference_snapshot(
                connection, voice_id, reference_emotion
            )
            self._insert_job(
                connection,
                job_id,
                client_id,
                project_id,
                created_by or client_id,
                script_id,
                voice_id,
                model_id,
                candidate_count,
                reference_emotion,
                len(items),
                timestamp,
                json.dumps(reference_files, separators=(",", ":")),
            )
            self._insert_items(connection, item_rows)
            self._insert_candidates(
                connection,
                item_rows,
                candidate_count,
                timestamp,
                settings_json,
                base_seed,
                candidate_count if seed_stride is None else seed_stride,
            )
        return job_id

    @staticmethod
    def _validate_create_arguments(
        items: list[ScriptItem],
        candidate_count: int,
        base_seed: int | None,
        seed_stride: int | None,
    ) -> None:
        if not items:
            raise ValueError("任务至少需要一条台词")
        if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
            raise ValueError("候选数量必须是整数")
        if not 1 <= candidate_count <= MAX_CANDIDATES_PER_ITEM:
            raise ValueError("候选数量必须在 1 到 4 之间")
        stride = candidate_count if seed_stride is None else seed_stride
        if isinstance(stride, bool) or not isinstance(stride, int):
            raise ValueError("随机种子步长必须是整数")
        if not candidate_count <= stride <= MAX_CANDIDATES_PER_ITEM:
            raise ValueError("随机种子步长必须覆盖候选数量且不超过 4")
        if base_seed is not None:
            if isinstance(base_seed, bool) or not isinstance(base_seed, int):
                raise ValueError("基准随机种子必须是整数")
            required_span = (len(items) - 1) * stride + candidate_count
            maximum = MAX_SEED - required_span + 1
            if base_seed < 0 or base_seed > maximum:
                raise ValueError(f"基准随机种子需在 0 到 {maximum} 之间")

    @staticmethod
    def _insert_items(connection: Any, rows: list[tuple[Any, ...]]) -> None:
        connection.executemany(
            """
            INSERT INTO job_items(
                id, job_id, sequence, source_line, text, pronunciation, generated_text,
                direction, emphasis, rewrite_instruction, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
            """,
            rows,
        )

    @classmethod
    def _insert_candidates(
        cls,
        connection: Any,
        item_rows: list[tuple[Any, ...]],
        candidate_count: int,
        timestamp: str,
        settings_json: str,
        base_seed: int | None,
        seed_stride: int,
    ) -> None:
        connection.executemany(
            """
            INSERT INTO job_item_candidates(
                id, job_item_id, source_candidate_id, origin_type, origin_id,
                kind, name, mode, ordinal, seed, text, pronunciation,
                generated_text, direction, emphasis, settings_json,
                generation_settings_json, status, submitted_at
            ) VALUES (?, ?, NULL, ?, ?, 'gpt', ?, 'initial', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
            """,
            cls._candidate_rows(
                item_rows,
                candidate_count,
                timestamp,
                settings_json,
                base_seed,
                seed_stride,
            ),
        )

    @staticmethod
    def _insert_job(
        connection: Any,
        job_id: str,
        client_id: str,
        project_id: str,
        created_by: str,
        script_id: str,
        voice_id: str,
        model_id: str,
        candidate_count: int,
        reference_emotion: str,
        item_count: int,
        timestamp: str,
        reference_files_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO jobs(
                id, client_id, project_id, created_by, script_id, voice_id, model_id, effect_id,
                effect_settings_json, candidate_count, reference_emotion,
                reference_files_json, reference_snapshot_version,
                status, total_items, completed_items, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'queued', ?, 0, ?)
            """,
            (
                job_id,
                client_id,
                project_id,
                created_by,
                script_id,
                voice_id,
                model_id,
                "clone_only",
                "{}",
                candidate_count,
                reference_emotion,
                reference_files_json,
                item_count,
                timestamp,
            ),
        )

    @staticmethod
    def _reference_snapshot(
        connection: Any, voice_id: str, reference_emotion: str
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM voice_files WHERE voice_id=? AND enabled=1"
        parameters: tuple[Any, ...] = (voice_id,)
        if reference_emotion != "all":
            query += " AND emotion_tag=?"
            parameters += (reference_emotion,)
        rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

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
            item.rewrite_instruction,
        )

    @staticmethod
    def _candidate_rows(
        item_rows: list[tuple[Any, ...]],
        candidate_count: int,
        timestamp: str,
        generation_settings_json: str,
        base_seed: int | None,
        seed_stride: int,
    ) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for item_index, item_row in enumerate(item_rows):
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
                        _candidate_seed(base_seed, item_index, ordinal, seed_stride),
                        item_row[4],
                        item_row[5],
                        item_row[6],
                        item_row[7],
                        item_row[8],
                        "{}",
                        generation_settings_json,
                        timestamp,
                    )
                )
        return rows

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._list_where("", (), limit)

    def list_for_client(self, client_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._list_where("WHERE j.client_id=?", (client_id,), limit)

    def list_for_project(
        self, project_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._list_where("WHERE j.project_id=?", (project_id,), limit)

    def list_for_script(self, script_id: str, limit: int = 300) -> list[dict[str, Any]]:
        return self._list_where("WHERE j.script_id=?", (script_id,), limit)

    def _list_where(
        self, where: str, parameters: tuple[Any, ...], limit: int
    ) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT j.*, s.name AS script_name, v.name AS voice_name,
                       COALESCE(p.name, j.project_id, '') AS project_name
                FROM jobs j
                JOIN scripts s ON s.id=j.script_id
                JOIN voices v ON v.id=j.voice_id
                LEFT JOIN projects p ON p.id=j.project_id
                {where}
                ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                         j.submitted_at DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def queued_ids(self) -> list[str]:
        return [str(row["id"]) for row in self.queued_entries()]

    def queued_entries(self) -> list[dict[str, str]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT id, submitted_at FROM jobs
                WHERE status='queued'
                ORDER BY submitted_at, id
                """
            ).fetchall()
        return [
            {"id": str(row["id"]), "submitted_at": str(row["submitted_at"] or "")}
            for row in rows
        ]

    def recover_interrupted(self) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='queued', started_at=NULL,
                    run_token='', error='服务器重启后重新排队' WHERE status='running'
                """
            )
            connection.execute(
                """
                UPDATE job_items SET status='queued', run_token=''
                WHERE status='running'
                """
            )
            connection.execute(
                """
                UPDATE job_item_candidates
                SET status='queued', started_at=NULL, run_token=''
                WHERE status='running'
                """
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
                       v.name AS voice_name,
                       COALESCE(p.name, j.project_id, '') AS project_name
                FROM jobs j
                JOIN scripts s ON s.id=j.script_id
                JOIN voices v ON v.id=j.voice_id
                LEFT JOIN projects p ON p.id=j.project_id
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

    def delete(self, job_id: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                DELETE FROM jobs
                WHERE id=?
                  AND status NOT IN ('queued', 'running')
                  AND NOT EXISTS (
                    SELECT 1 FROM job_items ji
                    LEFT JOIN job_item_candidates c ON c.job_item_id=ji.id
                    WHERE ji.job_id=?
                      AND (ji.status IN ('queued', 'running')
                           OR c.status IN ('queued', 'running'))
                  )
                """,
                (job_id, job_id),
            )
        return cursor.rowcount == 1

    def delete_unsubmitted(self, job_id: str) -> bool:
        """Remove a job created in the current request before it can run."""
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                DELETE FROM jobs
                WHERE id=? AND status='queued' AND started_at IS NULL
                  AND completed_items=0
                  AND NOT EXISTS (
                      SELECT 1 FROM job_items ji
                      LEFT JOIN job_item_candidates c ON c.job_item_id=ji.id
                      WHERE ji.job_id=?
                        AND (ji.status <> 'queued' OR c.status <> 'queued')
                  )
                """,
                (job_id, job_id),
            )
        return cursor.rowcount == 1

    def has_active_candidates(self, job_id: str) -> bool:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM job_items ji
                LEFT JOIN job_item_candidates c ON c.job_item_id=ji.id
                WHERE ji.job_id=?
                  AND (ji.status IN ('queued', 'running')
                       OR c.status IN ('queued', 'running')) LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return row is not None

    def has_active_for_script(self, script_id: str) -> bool:
        """Return whether a script still has queued or running generation work."""
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM jobs j
                LEFT JOIN job_items ji ON ji.job_id=j.id
                LEFT JOIN job_item_candidates c ON c.job_item_id=ji.id
                WHERE j.script_id=?
                  AND (j.status IN ('queued', 'running')
                       OR ji.status IN ('queued', 'running')
                       OR c.status IN ('queued', 'running'))
                LIMIT 1
                """,
                (script_id,),
            ).fetchone()
        return row is not None

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

    def delete_item(
        self, job_id: str, item_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
        """Delete one script line and return its artifacts plus job state."""
        with self._database.write() as connection:
            job = connection.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not job or str(job["status"]) in {"queued", "running"}:
                return None, [], False
            item = connection.execute(
                "SELECT * FROM job_items WHERE job_id=? AND id=?",
                (job_id, item_id),
            ).fetchone()
            if not item:
                return None, [], False
            candidates = connection.execute(
                "SELECT * FROM job_item_candidates WHERE job_item_id=?",
                (item_id,),
            ).fetchall()
            if str(item["status"]) in {"queued", "running"} or any(
                str(row["status"]) in {"queued", "running"} for row in candidates
            ):
                return None, [], False
            cursor = connection.execute(
                "DELETE FROM job_items WHERE job_id=? AND id=?",
                (job_id, item_id),
            )
            if cursor.rowcount != 1:  # pragma: no cover - guarded by the transaction
                return None, [], False
            remaining = connection.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(status='completed'), 0) AS completed "
                "FROM job_items WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if int(remaining["total"]):
                connection.execute(
                    "UPDATE jobs SET total_items=?, completed_items=? WHERE id=?",
                    (int(remaining["total"]), int(remaining["completed"]), job_id),
                )
                job_deleted = False
            else:
                connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                job_deleted = True
        return dict(item), [dict(row) for row in candidates], job_deleted

    def refresh_summary(self, job_id: str) -> None:
        with self._database.write() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(status='completed'), 0) AS completed
                FROM job_items WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
            if not row:
                return
            total = int(row["total"])
            completed = int(row["completed"])
            connection.execute(
                """
                UPDATE jobs SET total_items=?, completed_items=?,
                    status=CASE WHEN ? > 0 AND ? = ? AND status IN ('failed','completed')
                                THEN 'completed' ELSE status END,
                    finished_at=CASE WHEN ? > 0 AND ? = ? AND status IN ('failed','completed')
                                     THEN ? ELSE finished_at END,
                    error=CASE WHEN ? > 0 AND ? = ? AND status IN ('failed','completed')
                               THEN '' ELSE error END
                WHERE id=?
                """,
                (
                    total,
                    completed,
                    total,
                    completed,
                    total,
                    total,
                    completed,
                    total,
                    utc_now(),
                    total,
                    completed,
                    total,
                    job_id,
                ),
            )

    def set_running(self, job_id: str) -> str | None:
        """Claim a queued job and return its execution lease token."""
        token = uuid.uuid4().hex
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='running', started_at=?, run_token=?, error=''
                WHERE id=? AND status='queued'
                """,
                (utc_now(), token, job_id),
            )
        return token if cursor.rowcount == 1 else None

    def claim_running(self, job_id: str) -> str | None:
        return self.set_running(job_id)

    def fail_if_token(self, job_id: str, token: str, message: str) -> bool:
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status='failed', finished_at=?, eta_seconds=0,
                    run_token='', error=?
                WHERE id=? AND status='running' AND run_token=?
                """,
                (timestamp, message, job_id, token),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """
                    UPDATE job_items SET status='failed', run_token='', error=?
                    WHERE job_id=? AND status IN ('queued', 'running')
                    """,
                    (message, job_id),
                )
                connection.execute(
                    """
                    UPDATE job_item_candidates
                    SET status='failed', finished_at=?, run_token='', error=?,
                        raw_audio_path='', audio_path='', duration_seconds=NULL,
                        elapsed_seconds=NULL, processing_backend=''
                    WHERE job_item_id IN (
                        SELECT id FROM job_items WHERE job_id=?
                    ) AND status IN ('queued', 'running')
                    """,
                    (timestamp, message, job_id),
                )
        return cursor.rowcount == 1

    def fail_queued(self, job_id: str, message: str) -> bool:
        """Fail a queued job without touching a concurrently claimed run."""
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status='failed', finished_at=?, eta_seconds=0,
                    run_token='', error=?
                WHERE id=? AND status='queued'
                """,
                (timestamp, message, job_id),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """
                    UPDATE job_items SET status='failed', run_token='', error=?
                    WHERE job_id=? AND status='queued'
                    """,
                    (message, job_id),
                )
                connection.execute(
                    """
                    UPDATE job_item_candidates
                    SET status='failed', finished_at=?, run_token='', error=?,
                        raw_audio_path='', audio_path='', duration_seconds=NULL,
                        elapsed_seconds=NULL, processing_backend=''
                    WHERE job_item_id IN (
                        SELECT id FROM job_items WHERE job_id=?
                    ) AND status='queued'
                    """,
                    (timestamp, message, job_id),
                )
        return cursor.rowcount == 1

    def update_progress_if_token(
        self,
        job_id: str,
        token: str,
        completed_items: int,
        eta_seconds: int | None,
    ) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET completed_items=?, eta_seconds=?
                WHERE id=? AND status='running' AND run_token=?
                """,
                (completed_items, eta_seconds, job_id, token),
            )
        return cursor.rowcount == 1

    def finish_if_token(
        self, job_id: str, token: str, status: str, error: str = ""
    ) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status=?, finished_at=?, eta_seconds=0, run_token='', error=?
                WHERE id=? AND status='running' AND run_token=?
                """,
                (status, utc_now(), error, job_id, token),
            )
        return cursor.rowcount == 1

    def requeue_if_token(self, job_id: str, token: str, message: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status='queued', started_at=NULL, eta_seconds=NULL,
                    run_token='', error=?
                WHERE id=? AND status='running' AND run_token=?
                """,
                (message, job_id, token),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """
                    UPDATE job_items SET status='queued', run_token=''
                    WHERE job_id=? AND status='running'
                    """,
                    (job_id,),
                )
                connection.execute(
                    """
                    UPDATE job_item_candidates
                    SET status='queued', started_at=NULL, run_token='', error=?
                    WHERE job_item_id IN (
                        SELECT id FROM job_items WHERE job_id=?
                    ) AND status='running'
                    """,
                    (message, job_id),
                )
        return cursor.rowcount == 1

    def fail_active(self, job_id: str, message: str) -> bool:
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='failed', finished_at=?, eta_seconds=0,
                    run_token='', error=?
                WHERE id=? AND status IN ('queued', 'running')
                """,
                (timestamp, message, job_id),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """
                    UPDATE job_items SET status='failed', run_token='', error=?
                    WHERE job_id=? AND status IN ('queued', 'running')
                    """,
                    (message, job_id),
                )
                connection.execute(
                    """
                    UPDATE job_item_candidates
                    SET status='failed', finished_at=?, run_token='', error=?,
                        raw_audio_path='', audio_path='', duration_seconds=NULL,
                        elapsed_seconds=NULL, processing_backend=''
                    WHERE job_item_id IN (
                        SELECT id FROM job_items WHERE job_id=?
                    ) AND status IN ('queued', 'running')
                    """,
                    (timestamp, message, job_id),
                )
        return cursor.rowcount == 1

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
                UPDATE jobs SET status=?, finished_at=?, eta_seconds=0,
                    run_token='', error=? WHERE id=?
                """,
                (status, utc_now(), error, job_id),
            )

    def requeue(self, job_id: str, message: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE jobs SET status='queued', started_at=NULL,
                    eta_seconds=NULL, run_token='', error=? WHERE id=?
                """,
                (message, job_id),
            )
            connection.execute(
                """
                UPDATE job_items SET status='queued', run_token=''
                WHERE job_id=? AND status='running'
                """,
                (job_id,),
            )
            connection.execute(
                """
                UPDATE job_item_candidates
                SET status='queued', started_at=NULL, run_token='', error=?
                WHERE job_item_id IN (
                    SELECT id FROM job_items WHERE job_id=?
                ) AND status='running'
                """,
                (message, job_id),
            )

    def fail_pending_items(self, job_id: str, message: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE job_items SET status='failed', run_token='', error=?
                WHERE job_id=? AND status IN ('queued', 'running')
                """,
                (message, job_id),
            )
            connection.execute(
                """
                UPDATE job_item_candidates
                SET status='failed', finished_at=?, run_token='', error=?,
                    raw_audio_path='', audio_path='', duration_seconds=NULL,
                    elapsed_seconds=NULL, processing_backend=''
                WHERE job_item_id IN (
                    SELECT id FROM job_items WHERE job_id=?
                ) AND status IN ('queued', 'running')
                """,
                (utc_now(), message, job_id),
            )

    def set_item_running(self, item_id: str) -> str | None:
        """Claim a queued item and return its execution lease token."""
        token = uuid.uuid4().hex
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_items SET status='running', run_token=?, error=''
                WHERE id=? AND status='queued'
                """,
                (token, item_id),
            )
        return token if cursor.rowcount == 1 else None

    def claim_item_running(self, item_id: str) -> str | None:
        return self.set_item_running(item_id)

    def finish_item_if_token(
        self,
        item_id: str,
        token: str,
        status: str,
        audio_path: Path | None = None,
        raw_audio_path: Path | None = None,
        duration_seconds: float | None = None,
        elapsed_seconds: float | None = None,
        processing_backend: str = "",
        error: str = "",
    ) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_items SET status=?, audio_path=?, raw_audio_path=?,
                    duration_seconds=?, elapsed_seconds=?, processing_backend=?,
                    run_token='', error=?
                WHERE id=? AND status='running' AND run_token=?
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
                    token,
                ),
            )
        return cursor.rowcount == 1

    def requeue_missing_item(self, item_id: str) -> bool:
        """Requeue a completed item when its primary output file is gone."""
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_items
                SET status='queued', audio_path='', raw_audio_path='',
                    duration_seconds=NULL, elapsed_seconds=NULL,
                    processing_backend='', accepted_candidate_id='', run_token='', error=''
                WHERE id=? AND status='completed'
                """,
                (item_id,),
            )
        return cursor.rowcount == 1

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
                    elapsed_seconds=?, processing_backend=?, run_token='', error=? WHERE id=?
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
