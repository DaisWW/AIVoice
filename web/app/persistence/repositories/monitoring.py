from __future__ import annotations

from statistics import median
from typing import Any

from ..connection import SQLiteConnection


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
        value = (
            float(median(float(row["elapsed_seconds"]) for row in rows))
            if rows
            else 0.0
        )
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
                "SELECT * FROM jobs WHERE status='running' ORDER BY started_at LIMIT 1"
            ).fetchone()
            queued = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY submitted_at, id"
            ).fetchall()
        return running, queued

    @staticmethod
    def _queued_state(running: Any, queued: list[Any]) -> dict[str, tuple[int, int]]:
        workload = (
            max(0, running["total_items"] - running["completed_items"])
            if running
            else 0
        )
        result: dict[str, tuple[int, int]] = {}
        for position, row in enumerate(queued, start=1):
            result[str(row["id"])] = (position, workload)
            workload += int(row["total_items"])
        return result

    @classmethod
    def _job_snapshot(
        cls,
        job: dict[str, Any],
        queued_state: dict[str, tuple[int, int]],
        estimate: float,
    ) -> dict[str, Any]:
        if job["status"] == "running":
            remaining = max(0, int(job["total_items"]) - int(job["completed_items"]))
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
        queries = {
            "voices": "SELECT COUNT(*) FROM voices",
            "scripts": "SELECT COUNT(*) FROM scripts",
            "queued": "SELECT COUNT(*) FROM jobs WHERE status='queued'",
            "running": "SELECT COUNT(*) FROM jobs WHERE status='running'",
            "candidate_queued": "SELECT COUNT(*) FROM job_item_candidates WHERE origin_type='manual' AND kind='gpt' AND status='queued'",
            "candidate_running": "SELECT COUNT(*) FROM job_item_candidates WHERE origin_type='manual' AND kind='gpt' AND status='running'",
            "jobs": "SELECT COUNT(*) FROM jobs",
            "completed": "SELECT COUNT(*) FROM jobs WHERE status='completed'",
            "failed": "SELECT COUNT(*) FROM jobs WHERE status='failed'",
        }
        with self._database.read() as connection:
            return {
                key: int(connection.execute(query).fetchone()[0])
                for key, query in queries.items()
            }
