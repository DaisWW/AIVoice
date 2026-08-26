from __future__ import annotations

from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


class ScriptLineSelectionRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def list_for_script(self, script_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT s.script_id, s.sequence, s.job_id, s.job_item_id,
                       s.candidate_id, s.selected_by, s.selected_at,
                       u.display_name AS selected_by_name
                FROM script_line_selections s
                JOIN users u ON u.id=s.selected_by
                WHERE s.script_id=?
                ORDER BY s.sequence
                """,
                (script_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert(
        self,
        script_id: str,
        sequence: int,
        job_id: str,
        job_item_id: str,
        candidate_id: str,
        selected_by: str,
    ) -> dict[str, Any]:
        selected_at = utc_now()
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO script_line_selections(
                    script_id, sequence, job_id, job_item_id, candidate_id,
                    selected_by, selected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(script_id, sequence) DO UPDATE SET
                    job_id=excluded.job_id,
                    job_item_id=excluded.job_item_id,
                    candidate_id=excluded.candidate_id,
                    selected_by=excluded.selected_by,
                    selected_at=excluded.selected_at
                """,
                (
                    script_id,
                    sequence,
                    job_id,
                    job_item_id,
                    candidate_id,
                    selected_by,
                    selected_at,
                ),
            )
            row = connection.execute(
                """
                SELECT s.script_id, s.sequence, s.job_id, s.job_item_id,
                       s.candidate_id, s.selected_by, s.selected_at,
                       u.display_name AS selected_by_name
                FROM script_line_selections s
                JOIN users u ON u.id=s.selected_by
                WHERE s.script_id=? AND s.sequence=?
                """,
                (script_id, sequence),
            ).fetchone()
        return dict(row)

    def delete(self, script_id: str, sequence: int) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                "DELETE FROM script_line_selections WHERE script_id=? AND sequence=?",
                (script_id, sequence),
            )
        return cursor.rowcount == 1

    def clear_for_script(self, script_id: str) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                "DELETE FROM script_line_selections WHERE script_id=?",
                (script_id,),
            )
        return int(cursor.rowcount)
