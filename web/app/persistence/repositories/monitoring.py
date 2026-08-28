from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from ..connection import SQLiteConnection
from ...value_utils import stored_float, stored_int


class MonitoringRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def estimated_item_seconds(self) -> float:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT elapsed_seconds FROM job_items
                WHERE status='completed' AND elapsed_seconds IS NOT NULL
                ORDER BY rowid DESC LIMIT 200
                """
            ).fetchall()
        samples = [
            value
            for row in rows
            if (value := stored_float(row["elapsed_seconds"], 0.0, minimum=0)) > 0
        ]
        value = float(median(samples)) if samples else 0.0
        return min(max(value, 3.0), 180.0) if value else 18.0

    def queue_snapshot(self, job_id: str) -> dict[str, Any]:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if not row:
            return self._snapshot(None, None, self.estimated_item_seconds())
        snapshots = self.queue_snapshots([dict(row)])
        return snapshots.get(job_id, self._terminal_snapshot())

    def queue_snapshots(self, jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        active = [job for job in jobs if job["status"] in {"queued", "running"}]
        if not active:
            return {}
        estimate = self.estimated_item_seconds()
        running, queued = self._active_queue()
        queued_state = self._queued_state(running, queued)
        return {
            str(job["id"]): self._job_snapshot(job, queued_state, estimate)
            for job in active
        }

    def _active_queue(self) -> tuple[Any, list[Any]]:
        with self._database.read() as connection:
            running = connection.execute(
                """
                SELECT 'job' AS work_type, id,
                       MAX(total_items - completed_items, 0) AS remaining_items,
                       started_at AS ordered_at
                FROM jobs WHERE status='running'
                UNION ALL
                SELECT 'candidate' AS work_type, id, 1 AS remaining_items,
                       started_at AS ordered_at
                FROM job_item_candidates
                WHERE origin_type='manual' AND kind='gpt' AND status='running'
                ORDER BY ordered_at, id LIMIT 1
                """
            ).fetchone()
            queued = connection.execute(
                """
                SELECT 'job' AS work_type, id,
                       MAX(total_items - completed_items, 0) AS remaining_items,
                       submitted_at AS ordered_at
                FROM jobs WHERE status='queued'
                UNION ALL
                SELECT 'candidate' AS work_type, id, 1 AS remaining_items,
                       submitted_at AS ordered_at
                FROM job_item_candidates
                WHERE origin_type='manual' AND kind='gpt' AND status='queued'
                ORDER BY ordered_at, id
                """
            ).fetchall()
        return running, queued

    @staticmethod
    def _queued_state(running: Any, queued: list[Any]) -> dict[str, tuple[int, int]]:
        workload = stored_int(running["remaining_items"]) if running else 0
        result: dict[str, tuple[int, int]] = {}
        for position, row in enumerate(queued, start=1):
            if row["work_type"] == "job":
                result[str(row["id"])] = (position, workload)
            workload += stored_int(row["remaining_items"])
        return result

    @classmethod
    def _job_snapshot(
        cls,
        job: dict[str, Any],
        queued_state: dict[str, tuple[int, int]],
        estimate: float,
    ) -> dict[str, Any]:
        if job["status"] == "running":
            remaining = max(
                0,
                stored_int(job.get("total_items"))
                - stored_int(job.get("completed_items")),
            )
            return cls._snapshot(0, remaining * estimate, estimate)
        position, workload = queued_state.get(str(job["id"]), (None, None))
        wait = workload * estimate if workload is not None else None
        return cls._snapshot(position, wait, estimate)

    @staticmethod
    def _terminal_snapshot() -> dict[str, Any]:
        return {
            "queue_position": None,
            "estimated_wait_seconds": 0,
            "estimated_item_seconds": None,
        }

    @staticmethod
    def _snapshot(
        position: int | None, wait_seconds: float | None, estimate: float
    ) -> dict[str, Any]:
        return {
            "queue_position": position,
            "estimated_wait_seconds": (
                round(wait_seconds) if wait_seconds is not None else None
            ),
            "estimated_item_seconds": round(estimate, 1),
        }

    def counts(self) -> dict[str, int]:
        fields = (
            "voices",
            "scripts",
            "queued",
            "running",
            "candidate_queued",
            "candidate_running",
            "jobs",
            "completed",
            "failed",
        )
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM voices) AS voices,
                    (SELECT COUNT(*) FROM scripts) AS scripts,
                    (SELECT COUNT(*) FROM jobs WHERE status='queued') AS queued,
                    (SELECT COUNT(*) FROM jobs WHERE status='running') AS running,
                    (SELECT COUNT(*) FROM job_item_candidates
                     WHERE origin_type='manual' AND kind='gpt' AND status='queued')
                        AS candidate_queued,
                    (SELECT COUNT(*) FROM job_item_candidates
                     WHERE origin_type='manual' AND kind='gpt' AND status='running')
                        AS candidate_running,
                    COUNT(*) AS jobs,
                    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                FROM jobs
                """
            ).fetchone()
        return {field: stored_int(row[field]) for field in fields}

    def admin_insights(self, days: int = 14) -> dict[str, Any]:
        """Return small, read-only aggregates used by the admin control room."""
        window = max(2, min(days, 31))
        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=window - 1)).isoformat(timespec="microseconds")
        rows = self._read_admin_insights(cutoff, now.isoformat(timespec="microseconds"))
        return {
            "window_days": window,
            "activity": self._activity_payload(rows["activity"], now.date(), window),
            "failure_reasons": [
                {"reason": str(row["reason"]), "count": stored_int(row["count"])}
                for row in rows["failures"]
            ],
            "top_users": self._ranking_payload(rows["users"], "user_id"),
            "top_projects": self._ranking_payload(rows["projects"], "project_id"),
            "active_sessions": stored_int(rows["active_sessions"]),
            "last_job_at": str(rows["last_job_at"]) if rows["last_job_at"] else None,
            "last_audit_at": (
                str(rows["last_audit_at"]) if rows["last_audit_at"] else None
            ),
        }

    def _read_admin_insights(self, cutoff: str, now: str) -> dict[str, Any]:
        with self._database.read() as connection:
            activity_rows = self._activity_rows(connection, cutoff)
            failure_rows = self._failure_rows(connection)
            user_rows = self._user_rows(connection)
            project_rows = self._project_rows(connection)
            active_sessions = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (now,)
            ).fetchone()[0]
            last_job_at = connection.execute(
                "SELECT MAX(submitted_at) FROM jobs"
            ).fetchone()[0]
            last_audit_at = connection.execute(
                "SELECT MAX(created_at) FROM audit_logs"
            ).fetchone()[0]
        return {
            "activity": activity_rows,
            "failures": failure_rows,
            "users": user_rows,
            "projects": project_rows,
            "active_sessions": active_sessions,
            "last_job_at": last_job_at,
            "last_audit_at": last_audit_at,
        }

    @staticmethod
    def _activity_rows(connection: Any, cutoff: str) -> list[Any]:
        return connection.execute(
            """
            SELECT substr(submitted_at, 1, 10) AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
            FROM jobs
            WHERE submitted_at >= ?
            GROUP BY day
            ORDER BY day
            """,
            (cutoff,),
        ).fetchall()

    @staticmethod
    def _failure_rows(connection: Any) -> list[Any]:
        return connection.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(error), ''), '未提供错误信息') AS reason,
                   COUNT(*) AS count
            FROM jobs
            WHERE status='failed'
            GROUP BY reason
            ORDER BY count DESC, reason
            LIMIT 6
            """
        ).fetchall()

    @staticmethod
    def _user_rows(connection: Any) -> list[Any]:
        return connection.execute(
            """
            SELECT j.client_id AS user_id,
                   COALESCE(NULLIF(u.display_name, ''), j.client_id) AS name,
                   COUNT(*) AS jobs,
                   SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END) AS failed
            FROM jobs j
            LEFT JOIN users u ON u.id=j.client_id
            GROUP BY j.client_id, u.display_name
            ORDER BY jobs DESC, name COLLATE NOCASE
            LIMIT 6
            """
        ).fetchall()

    @staticmethod
    def _project_rows(connection: Any) -> list[Any]:
        return connection.execute(
            """
            SELECT j.project_id AS project_id,
                   COALESCE(NULLIF(p.name, ''), NULLIF(j.project_id, ''), '未分配项目') AS name,
                   COUNT(*) AS jobs,
                   SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END) AS failed
            FROM jobs j
            LEFT JOIN projects p ON p.id=j.project_id
            GROUP BY j.project_id, p.name
            ORDER BY jobs DESC, name COLLATE NOCASE
            LIMIT 6
            """
        ).fetchall()

    @staticmethod
    def _activity_payload(
        rows: list[Any], today: Any, window: int
    ) -> list[dict[str, Any]]:
        activity_by_day = {str(row["day"]): dict(row) for row in rows}
        result = []
        for offset in range(window - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            row = activity_by_day.get(day, {})
            result.append(
                {
                    "day": day,
                    "total": stored_int(row.get("total")),
                    "completed": stored_int(row.get("completed")),
                    "failed": stored_int(row.get("failed")),
                }
            )
        return result

    @staticmethod
    def _ranking_payload(rows: list[Any], source_id: str) -> list[dict[str, Any]]:
        return [
            {
                source_id: str(row[source_id] or ""),
                "name": str(row["name"]),
                "jobs": stored_int(row["jobs"]),
                "completed": stored_int(row["completed"]),
                "failed": stored_int(row["failed"]),
            }
            for row in rows
        ]
