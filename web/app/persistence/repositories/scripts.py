from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...domain import ScriptItem
from ..common import utc_now
from ..connection import SQLiteConnection


class ScriptRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def create(
        self,
        name: str,
        original_name: str,
        source_path: Path,
        owner_id: str,
        source_kind: str,
        items: Sequence[ScriptItem],
        default_voice_id: str | None = None,
        default_effect_id: str | None = None,
        script_id: str | None = None,
        project_id: str = "",
    ) -> str:
        script_id = script_id or f"script-{uuid.uuid4().hex[:12]}"
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO scripts(
                    id, name, original_name, source_path, owner_id, project_id, source_kind,
                    default_voice_id, default_effect_id, item_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    script_id,
                    name.strip(),
                    original_name,
                    str(source_path),
                    owner_id,
                    project_id,
                    source_kind,
                    default_voice_id,
                    default_effect_id,
                    len(items),
                    utc_now(),
                ),
            )
        return script_id

    def get(self, script_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT * FROM scripts WHERE id=?", (script_id,)
            ).fetchone()
        return dict(row) if row else None

    def update(self, script_id: str, name: str, default_voice_id: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                "UPDATE scripts SET name=?, default_voice_id=? WHERE id=?",
                (name.strip(), default_voice_id, script_id),
            )
        return cursor.rowcount == 1

    def list(
        self, project_id: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        where = "WHERE project_id=?" if project_id is not None else ""
        parameters = (project_id,) if project_id is not None else ()
        limit_clause = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            parameters += (limit,)
        with self._database.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM scripts {where} ORDER BY created_at DESC{limit_clause}",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]
