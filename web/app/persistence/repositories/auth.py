from __future__ import annotations

import uuid
from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


class AuthRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def count_users(self) -> int:
        with self._database.read() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def overview_counts(self) -> dict[str, int]:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS users,
                       SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active_users
                FROM users
                """
            ).fetchone()
        return {
            "users": int(row["users"] or 0),
            "active_users": int(row["active_users"] or 0),
        }

    def create_user(
        self,
        username: str,
        display_name: str,
        password_hash: str,
        *,
        role: str = "member",
        must_change_password: bool = True,
    ) -> dict[str, Any]:
        user_id = f"user-{uuid.uuid4().hex[:12]}"
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO users(
                    id, username, display_name, password_hash, role, status,
                    must_change_password, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    user_id,
                    username.strip().lower(),
                    display_name.strip(),
                    password_hash,
                    role,
                    int(must_change_password),
                    timestamp,
                    timestamp,
                ),
            )
        user = self.get_user(user_id)
        if not user:  # pragma: no cover - guarded by the insert
            raise RuntimeError("账户创建后未找到")
        return user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._one("u.id=?", (user_id,))

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        return self._one("u.username=? COLLATE NOCASE", (username.strip(),))

    def _one(self, where: str, parameters: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                f"""
                SELECT u.*,
                       (SELECT COUNT(*) FROM project_members pm WHERE pm.user_id=u.id)
                           AS project_count
                       ,(SELECT COUNT(*) FROM jobs j
                         WHERE j.client_id=u.id OR j.created_by=u.id) AS job_count
                       ,(SELECT COUNT(*) FROM voices v WHERE v.owner_id=u.id)
                           AS voice_count
                       ,(SELECT COUNT(*) FROM scripts s WHERE s.owner_id=u.id)
                           AS script_count
                       ,(SELECT MAX(a.created_at) FROM audit_logs a
                         WHERE a.actor_user_id=u.id) AS last_activity_at
                FROM users u WHERE {where}
                """,
                parameters,
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT u.*,
                       (SELECT COUNT(*) FROM project_members pm WHERE pm.user_id=u.id)
                           AS project_count
                       ,(SELECT COUNT(*) FROM jobs j
                         WHERE j.client_id=u.id OR j.created_by=u.id) AS job_count
                       ,(SELECT COUNT(*) FROM voices v WHERE v.owner_id=u.id)
                           AS voice_count
                       ,(SELECT COUNT(*) FROM scripts s WHERE s.owner_id=u.id)
                           AS script_count
                       ,(SELECT MAX(a.created_at) FROM audit_logs a
                         WHERE a.actor_user_id=u.id) AS last_activity_at
                FROM users u
                ORDER BY CASE u.role WHEN 'system_admin' THEN 0 ELSE 1 END,
                         u.status, u.display_name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def update_password(
        self,
        user_id: str,
        password_hash: str,
        *,
        must_change: bool = False,
        invalidate_sessions: bool = True,
    ) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE users SET password_hash=?, must_change_password=?, updated_at=?
                WHERE id=?
                """,
                (password_hash, int(must_change), utc_now(), user_id),
            )
            if invalidate_sessions:
                connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return cursor.rowcount == 1

    def update_status(self, user_id: str, status: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                "UPDATE users SET status=?, updated_at=? WHERE id=?",
                (status, utc_now(), user_id),
            )
            if status != "active":
                connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return cursor.rowcount == 1

    def note_login(self, user_id: str) -> None:
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute(
                "UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
                (timestamp, timestamp, user_id),
            )

    def create_session(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
        ip_address: str,
        user_agent: str,
    ) -> None:
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at<=?", (timestamp,))
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, user_id, created_at, expires_at, last_seen_at,
                    ip_address, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    user_id,
                    timestamp,
                    expires_at,
                    timestamp,
                    ip_address,
                    user_agent[:300],
                ),
            )

    def user_for_session(self, token_hash: str) -> dict[str, Any] | None:
        timestamp = utc_now()
        with self._database.write() as connection:
            row = connection.execute(
                """
                SELECT u.* FROM sessions s
                JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'
                """,
                (token_hash, timestamp),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
                    (timestamp, token_hash),
                )
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._database.write() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
