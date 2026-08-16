from __future__ import annotations

import uuid
from typing import Any

from ..common import utc_now
from ..connection import SQLiteConnection


LEGACY_PROJECT_ID = "project-legacy"


class ProjectRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def ensure_legacy_project(self, owner_id: str) -> None:
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO projects(
                    id, name, description, owner_id, status, created_at, updated_at
                ) VALUES (?, '历史项目', '升级前已有的声音库、台本与生成记录', ?,
                          'active', ?, ?)
                """,
                (LEGACY_PROJECT_ID, owner_id, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO project_members(
                    project_id, user_id, role, added_by, joined_at
                ) VALUES (?, ?, 'owner', ?, ?)
                """,
                (LEGACY_PROJECT_ID, owner_id, owner_id, timestamp),
            )
            for table in ("voices", "scripts", "jobs"):
                connection.execute(
                    f"UPDATE {table} SET project_id=? WHERE project_id=''",
                    (LEGACY_PROJECT_ID,),
                )
            connection.execute(
                "UPDATE jobs SET created_by=client_id WHERE created_by=''"
            )

    def create(self, owner_id: str, name: str, description: str) -> dict[str, Any]:
        project_id = f"project-{uuid.uuid4().hex[:12]}"
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    id, name, description, owner_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    project_id,
                    name.strip(),
                    description.strip(),
                    owner_id,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO project_members(
                    project_id, user_id, role, added_by, joined_at
                ) VALUES (?, ?, 'owner', ?, ?)
                """,
                (project_id, owner_id, owner_id, timestamp),
            )
        project = self.get(project_id)
        if not project:  # pragma: no cover
            raise RuntimeError("项目创建后未找到")
        return project

    def get(self, project_id: str) -> dict[str, Any] | None:
        rows = self._list("WHERE p.id=?", (project_id,))
        return rows[0] if rows else None

    def update(self, project_id: str, name: str, description: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE projects SET name=?, description=?, updated_at=? WHERE id=?
                """,
                (name.strip(), description.strip(), utc_now(), project_id),
            )
        return cursor.rowcount == 1

    def list_for_user(
        self, user_id: str, *, include_all: bool = False
    ) -> list[dict[str, Any]]:
        if include_all:
            return self._list("", ())
        return self._list(
            "JOIN project_members mine ON mine.project_id=p.id WHERE mine.user_id=?",
            (user_id,),
        )

    def _list(self, clause: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, u.display_name AS owner_name, u.username AS owner_username,
                       (SELECT COUNT(*) FROM project_members pm WHERE pm.project_id=p.id)
                           AS member_count,
                       (SELECT COUNT(*) FROM project_members pm
                        JOIN users pu ON pu.id=pm.user_id
                        WHERE pm.project_id=p.id AND pu.status='active')
                           AS active_member_count,
                       (SELECT COUNT(*) FROM voices v WHERE v.project_id=p.id)
                           AS voice_count,
                       (SELECT COUNT(*) FROM scripts s WHERE s.project_id=p.id)
                           AS script_count,
                       (SELECT COUNT(*) FROM jobs j WHERE j.project_id=p.id)
                           AS job_count
                       ,(SELECT MAX(j.submitted_at) FROM jobs j
                         WHERE j.project_id=p.id) AS last_job_at
                       ,(SELECT COUNT(*) FROM project_invitations pi
                         WHERE pi.project_id=p.id AND pi.status='pending')
                           AS pending_invitation_count
                FROM projects p
                JOIN users u ON u.id=p.owner_id
                {clause}
                ORDER BY p.updated_at DESC, p.name COLLATE NOCASE
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def role(self, project_id: str, user_id: str) -> str | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT role FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
        return str(row["role"]) if row else None

    def default_for_user(
        self, user_id: str, *, include_all: bool = False
    ) -> str | None:
        projects = self.list_for_user(user_id, include_all=include_all)
        if not projects:
            return None
        legacy = next(
            (project for project in projects if project["id"] == LEGACY_PROJECT_ID),
            None,
        )
        return str((legacy or projects[0])["id"])

    def members(self, project_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT u.id, u.username, u.display_name, u.status, pm.role,
                       pm.joined_at, pm.added_by
                FROM project_members pm
                JOIN users u ON u.id=pm.user_id
                WHERE pm.project_id=?
                ORDER BY CASE pm.role WHEN 'owner' THEN 0 ELSE 1 END,
                         u.display_name COLLATE NOCASE
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def invite(
        self, project_id: str, invited_user_id: str, invited_by: str
    ) -> dict[str, Any]:
        invitation_id = f"invite-{uuid.uuid4().hex[:12]}"
        with self._database.write() as connection:
            member = connection.execute(
                "SELECT 1 FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, invited_user_id),
            ).fetchone()
            if member:
                raise ValueError("该账户已经是项目成员")
            pending = connection.execute(
                """
                SELECT 1 FROM project_invitations
                WHERE project_id=? AND invited_user_id=? AND status='pending'
                """,
                (project_id, invited_user_id),
            ).fetchone()
            if pending:
                raise ValueError("已经向该账户发送过邀请")
            connection.execute(
                """
                INSERT INTO project_invitations(
                    id, project_id, invited_user_id, invited_by, status, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (invitation_id, project_id, invited_user_id, invited_by, utc_now()),
            )
        invitation = self.invitation(invitation_id)
        if not invitation:  # pragma: no cover
            raise RuntimeError("邀请创建后未找到")
        return invitation

    def invitation(self, invitation_id: str) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT i.*, p.name AS project_name,
                       target.username AS invited_username,
                       target.display_name AS invited_display_name,
                       actor.display_name AS inviter_name
                FROM project_invitations i
                JOIN projects p ON p.id=i.project_id
                JOIN users target ON target.id=i.invited_user_id
                JOIN users actor ON actor.id=i.invited_by
                WHERE i.id=?
                """,
                (invitation_id,),
            ).fetchone()
        return dict(row) if row else None

    def invitations_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT i.*, p.name AS project_name, p.description AS project_description,
                       actor.display_name AS inviter_name
                FROM project_invitations i
                JOIN projects p ON p.id=i.project_id
                JOIN users actor ON actor.id=i.invited_by
                WHERE i.invited_user_id=? AND i.status='pending'
                ORDER BY i.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def respond(self, invitation_id: str, user_id: str, accept: bool) -> dict[str, Any]:
        status = "accepted" if accept else "declined"
        timestamp = utc_now()
        with self._database.write() as connection:
            invitation = connection.execute(
                """
                SELECT * FROM project_invitations
                WHERE id=? AND invited_user_id=? AND status='pending'
                """,
                (invitation_id, user_id),
            ).fetchone()
            if not invitation:
                raise ValueError("邀请不存在或已经处理")
            connection.execute(
                "UPDATE project_invitations SET status=?, responded_at=? WHERE id=?",
                (status, timestamp, invitation_id),
            )
            if accept:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO project_members(
                        project_id, user_id, role, added_by, joined_at
                    ) VALUES (?, ?, 'member', ?, ?)
                    """,
                    (
                        invitation["project_id"],
                        user_id,
                        invitation["invited_by"],
                        timestamp,
                    ),
                )
        result = self.invitation(invitation_id)
        if not result:  # pragma: no cover
            raise RuntimeError("邀请处理后未找到")
        return result

    def remove_member(self, project_id: str, user_id: str) -> bool:
        with self._database.write() as connection:
            row = connection.execute(
                "SELECT owner_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if row and str(row["owner_id"]) == user_id:
                raise ValueError("不能移除项目负责人")
            cursor = connection.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            )
        return cursor.rowcount == 1
