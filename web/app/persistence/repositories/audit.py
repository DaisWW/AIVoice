from __future__ import annotations

import json
from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


class AuditRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def record(
        self,
        action: str,
        *,
        actor: dict[str, Any] | None = None,
        target_type: str = "",
        target_id: str = "",
        project_id: str | None = None,
        ip_address: str = "",
        success: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = {
            key: value
            for key, value in (details or {}).items()
            if key.lower() not in {"password", "password_hash", "token", "api_key"}
        }
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs(
                    actor_user_id, actor_name, action, target_type, target_id,
                    project_id, ip_address, success, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    actor.get("id") if actor else None,
                    str(actor.get("display_name") or actor.get("username") or "")
                    if actor
                    else "",
                    action,
                    target_type,
                    target_id,
                    project_id,
                    ip_address,
                    int(success),
                    json.dumps(safe_details, ensure_ascii=False, separators=(",", ":")),
                    utc_now(),
                ),
            )

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 1000),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(str(item.pop("details_json") or "{}"))
            except json.JSONDecodeError:
                item["details"] = {}
            item["success"] = bool(item["success"])
            result.append(item)
        return result
