from __future__ import annotations

import json
import uuid
from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


class TextGenerationRunRepository:
    """Durable state for remote text-model requests and their drafts."""

    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def create(
        self,
        *,
        project_id: str,
        script_id: str | None,
        parent_run_id: str | None = None,
        created_by: str,
        kind: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = f"text-run-{uuid.uuid4().hex[:12]}"
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO text_generation_runs(
                    id, project_id, script_id, parent_run_id, created_by, kind, status, stage,
                    input_json, context_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', '排队中', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    script_id,
                    parent_run_id,
                    created_by,
                    kind,
                    json.dumps(input_data, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                ),
            )
        result = self.get(run_id)
        if result is None:  # pragma: no cover
            raise RuntimeError("文本生成记录创建后未找到")
        return result

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT r.*, u.display_name AS created_by_name,
                       u.username AS created_by_username
                FROM text_generation_runs r
                JOIN users u ON u.id=r.created_by
                WHERE r.id=?
                """,
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_for_project(
        self,
        project_id: str,
        *,
        script_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["r.project_id=?"]
        parameters: list[Any] = [project_id]
        if script_id is not None:
            clauses.append("r.script_id=?")
            parameters.append(script_id)
        parameters.append(min(max(int(limit), 1), 300))
        with self._database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, u.display_name AS created_by_name,
                       u.username AS created_by_username
                FROM text_generation_runs r
                JOIN users u ON u.id=r.created_by
                WHERE {' AND '.join(clauses)}
                ORDER BY r.updated_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_for_script(self, script_id: str) -> int:
        """Remove text history together with a deleted script."""
        with self._database.write() as connection:
            cursor = connection.execute(
                "DELETE FROM text_generation_runs WHERE script_id=?", (script_id,)
            )
        return cursor.rowcount

    def has_active_for_script(self, script_id: str) -> bool:
        """Return whether a script has queued or running text generation."""
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM text_generation_runs
                WHERE script_id=? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (script_id,),
            ).fetchone()
        return row is not None

    def mark_running(self, run_id: str, stage: str = "模型生成中") -> bool:
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE text_generation_runs
                SET status='running', stage=?, started_at=COALESCE(started_at, ?),
                    updated_at=?, error=''
                WHERE id=? AND status='queued'
                """,
                (stage, timestamp, timestamp, run_id),
            )
        return cursor.rowcount == 1

    def update_progress(
        self, run_id: str, *, stage: str | None = None, output_text: str | None = None
    ) -> bool:
        fields: list[str] = []
        parameters: list[Any] = []
        if stage is not None:
            fields.append("stage=?")
            parameters.append(stage)
        if output_text is not None:
            fields.append("output_text=?")
            parameters.append(output_text)
        if not fields:
            return True
        fields.append("updated_at=?")
        parameters.append(utc_now())
        parameters.append(run_id)
        with self._database.write() as connection:
            cursor = connection.execute(
                f"UPDATE text_generation_runs SET {', '.join(fields)} WHERE id=? AND status='running'",
                parameters,
            )
        return cursor.rowcount == 1

    def complete(
        self,
        run_id: str,
        *,
        output_text: str,
        result: Any,
        stage: str = "已完成",
    ) -> bool:
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE text_generation_runs
                SET status='completed', stage=?, output_text=?, result_json=?,
                    error='', updated_at=?, finished_at=?
                WHERE id=? AND status='running'
                """,
                (
                    stage,
                    output_text,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                    run_id,
                ),
            )
        return cursor.rowcount == 1

    def fail(
        self,
        run_id: str,
        *,
        error: str,
        output_text: str = "",
        stage: str = "失败",
    ) -> bool:
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE text_generation_runs
                SET status='failed', stage=?, output_text=?, error=?,
                    updated_at=?, finished_at=?
                WHERE id=? AND status IN ('queued', 'running')
                """,
                (stage, output_text, error, timestamp, timestamp, run_id),
            )
        return cursor.rowcount == 1

    def recover_interrupted(self) -> int:
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE text_generation_runs
                SET status='failed', stage='服务已重启',
                    error='服务重启时未完成，可从历史记录重试',
                    updated_at=?, finished_at=?
                WHERE status IN ('queued', 'running')
                """,
                (timestamp, timestamp),
            )
        return cursor.rowcount

    def fail_unfinished(
        self,
        *,
        error: str = "服务停止时未完成，可从历史记录重试",
        stage: str = "服务已停止",
    ) -> int:
        """Mark work that cannot survive an in-process shutdown as failed."""
        timestamp = utc_now()
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE text_generation_runs
                SET status='failed', stage=?, error=?, updated_at=?, finished_at=?
                WHERE status IN ('queued', 'running')
                """,
                (stage, error, timestamp, timestamp),
            )
        return cursor.rowcount
