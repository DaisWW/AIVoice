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
        with self._database.write() as connection:
            legacy_tables = ("voices", "scripts", "jobs")
            unassigned = sum(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE COALESCE(project_id, '')=''"
                ).fetchone()[0]
                for table in legacy_tables
            )
            existing = connection.execute(
                "SELECT 1 FROM projects WHERE id=?", (LEGACY_PROJECT_ID,)
            ).fetchone()
            if not existing and not unassigned:
                return
            timestamp = utc_now()
            if not existing:
                connection.execute(
                    """
                    INSERT INTO projects(
                        id, name, description, owner_id, status, created_at, updated_at
                    ) VALUES (?, '历史项目', '升级前已有的声音库、台本与生成记录', ?,
                              'active', ?, ?)
                    """,
                    (LEGACY_PROJECT_ID, owner_id, timestamp, timestamp),
                )
                connection.execute(
                    """
                    INSERT INTO project_members(
                        project_id, user_id, role, added_by, joined_at
                    ) VALUES (?, ?, 'owner', ?, ?)
                    """,
                    (LEGACY_PROJECT_ID, owner_id, owner_id, timestamp),
                )
            for table in legacy_tables:
                connection.execute(
                    f"UPDATE {table} SET project_id=? WHERE COALESCE(project_id, '')=''",
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

    def overview_counts(self) -> dict[str, int]:
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT (SELECT COUNT(*) FROM projects) AS projects,
                       (SELECT COUNT(*) FROM project_members) AS memberships
                """
            ).fetchone()
        return {
            "projects": int(row["projects"] or 0),
            "memberships": int(row["memberships"] or 0),
        }

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
                ORDER BY CASE pm.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                         u.display_name COLLATE NOCASE
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_member(self, project_id: str, user_id: str, added_by: str) -> None:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO project_members(
                    project_id, user_id, role, added_by, joined_at
                ) VALUES (?, ?, 'member', ?, ?)
                """,
                (project_id, user_id, added_by, utc_now()),
            )
            if cursor.rowcount != 1:
                raise ValueError("该账户已经是项目成员")

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

    def update_member_role(self, project_id: str, user_id: str, role: str) -> bool:
        if role not in {"admin", "member"}:
            raise ValueError("不支持的项目权限")
        with self._database.write() as connection:
            project = connection.execute(
                "SELECT owner_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project and str(project["owner_id"]) == user_id:
                raise ValueError("项目所有者的权限不能修改")
            cursor = connection.execute(
                """
                UPDATE project_members SET role=?
                WHERE project_id=? AND user_id=?
                """,
                (role, project_id, user_id),
            )
        return cursor.rowcount == 1
