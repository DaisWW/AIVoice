from __future__ import annotations

import uuid
from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


class ContextRevisionRepository:
    """Persist project and script context snapshots for iterative editing."""

    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def record(
        self,
        *,
        scope: str,
        project_id: str,
        script_id: str | None,
        content: str,
        created_by: str,
    ) -> dict[str, Any] | None:
        normalized_scope = str(scope).strip()
        if normalized_scope not in {"project", "script"}:
            raise ValueError("不支持的上下文范围")
        normalized_script_id = str(script_id or "") or None
        if normalized_scope == "project":
            normalized_script_id = None
        if normalized_scope == "script" and not normalized_script_id:
            raise ValueError("角色上下文缺少台本")
        normalized_content = str(content or "").strip()
        with self._database.write() as connection:
            latest = connection.execute(
                """
                SELECT version, content
                FROM context_revisions
                WHERE scope=? AND project_id=?
                  AND ((script_id IS NULL AND ? IS NULL) OR script_id=?)
                ORDER BY version DESC
                LIMIT 1
                """,
                (
                    normalized_scope,
                    project_id,
                    normalized_script_id,
                    normalized_script_id,
                ),
            ).fetchone()
            if latest and str(latest["content"] or "") == normalized_content:
                row = connection.execute(
                    """
                    SELECT * FROM context_revisions
                    WHERE scope=? AND project_id=?
                      AND ((script_id IS NULL AND ? IS NULL) OR script_id=?)
                      AND version=?
                    """,
                    (
                        normalized_scope,
                        project_id,
                        normalized_script_id,
                        normalized_script_id,
                        int(latest["version"]),
                    ),
                ).fetchone()
                return dict(row) if row else None
            version = int(latest["version"] or 0) + 1 if latest else 1
            revision_id = f"context-{uuid.uuid4().hex[:12]}"
            timestamp = utc_now()
            connection.execute(
                """
                INSERT INTO context_revisions(
                    id, scope, project_id, script_id, version, content,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    normalized_scope,
                    project_id,
                    normalized_script_id,
                    version,
                    normalized_content,
                    created_by,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM context_revisions WHERE id=?", (revision_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_for_project(
        self, project_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT r.*, u.display_name AS created_by_name,
                       u.username AS created_by_username
                FROM context_revisions r
                JOIN users u ON u.id=r.created_by
                WHERE r.project_id=? AND r.scope='project'
                ORDER BY r.version DESC
                LIMIT ?
                """,
                (project_id, min(max(int(limit), 1), 300)),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_for_script(self, script_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT r.*, u.display_name AS created_by_name,
                       u.username AS created_by_username
                FROM context_revisions r
                JOIN users u ON u.id=r.created_by
                WHERE r.script_id=? AND r.scope='script'
                ORDER BY r.version DESC
                LIMIT ?
                """,
                (script_id, min(max(int(limit), 1), 300)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, revision_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT r.*, u.display_name AS created_by_name,
                       u.username AS created_by_username
                FROM context_revisions r
                JOIN users u ON u.id=r.created_by
                WHERE r.id=?
                """,
                (revision_id,),
            ).fetchone()
        return dict(row) if row else None
