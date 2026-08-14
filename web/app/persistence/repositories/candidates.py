from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ...domain import ScriptItem
from ..common import utc_now
from ..connection import SQLiteConnection


class CandidateRepository:
    """Persistence for GPT-SoVITS takes belonging to one script line."""

    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT c.*, ji.job_id, ji.sequence,
                       ji.accepted_candidate_id
                FROM job_item_candidates c
                JOIN job_items ji ON ji.id=c.job_item_id
                WHERE ji.job_id=?
                ORDER BY ji.sequence, c.ordinal, c.submitted_at, c.id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_for_item(self, job_item_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT c.*, ji.job_id, ji.sequence,
                       ji.accepted_candidate_id
                FROM job_item_candidates c
                JOIN job_items ji ON ji.id=c.job_item_id
                WHERE c.job_item_id=?
                ORDER BY c.ordinal, c.submitted_at, c.id
                """,
                (job_item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        return self._get_where("c.id=?", (candidate_id,))

    def get_for_job(self, job_id: str, candidate_id: str) -> dict[str, Any] | None:
        return self._get_where(
            "ji.job_id=? AND c.id=?",
            (job_id, candidate_id),
        )

    def _get_where(
        self, where: str, parameters: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        with self._database.read() as connection:
            row = connection.execute(
                f"""
                SELECT c.*, ji.job_id, ji.sequence, ji.accepted_candidate_id,
                       j.voice_id, j.model_id, j.reference_emotion
                FROM job_item_candidates c
                JOIN job_items ji ON ji.id=c.job_item_id
                JOIN jobs j ON j.id=ji.job_id
                WHERE {where}
                """,
                parameters,
            ).fetchone()
        return dict(row) if row else None

    def create_regeneration(
        self,
        job_item: dict[str, Any],
        script_item: ScriptItem,
        seed: int,
        generation_settings: dict[str, float | int],
        name: str = "",
        source_candidate_id: str | None = None,
    ) -> str:
        candidate_id = f"candidate-{uuid.uuid4().hex[:14]}"
        with self._database.write() as connection:
            ordinal = self._next_ordinal(connection, str(job_item["id"]))
            resolved_name = name.strip() or f"重做 {ordinal}"
            connection.execute(
                """
                INSERT INTO job_item_candidates(
                    id, job_item_id, source_candidate_id, origin_type, origin_id,
                    kind, name, mode, ordinal, seed, text, pronunciation,
                    generated_text, direction, emphasis, settings_json,
                    generation_settings_json, status, submitted_at
                ) VALUES (?, ?, ?, 'manual', ?, 'gpt', ?, 'generation', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    candidate_id,
                    job_item["id"],
                    source_candidate_id,
                    candidate_id,
                    resolved_name,
                    ordinal,
                    seed,
                    script_item.text,
                    script_item.pronunciation,
                    script_item.generated_text,
                    script_item.direction,
                    ",".join(script_item.emphasis),
                    "{}",
                    json.dumps(generation_settings, separators=(",", ":")),
                    utc_now(),
                ),
            )
        return candidate_id

    @staticmethod
    def _next_ordinal(connection: Any, job_item_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS value FROM job_item_candidates WHERE job_item_id=?",
            (job_item_id,),
        ).fetchone()
        return int(row["value"])

    def queued_manual_ids(self, kind: str) -> list[str]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT id FROM job_item_candidates
                WHERE origin_type='manual' AND kind=? AND status='queued'
                ORDER BY submitted_at, id
                """,
                (kind,),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def recover_interrupted(self) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='queued', started_at=NULL,
                    error='服务器重启后重新排队'
                WHERE kind='gpt' AND status='running'
                """
            )
        return int(cursor.rowcount)

    def set_running(self, candidate_id: str) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE job_item_candidates
                SET status='running', started_at=?, error=''
                WHERE id=? AND status='queued'
                """,
                (utc_now(), candidate_id),
            )

    def finish(
        self,
        candidate_id: str,
        status: str,
        raw_audio_path: Path | None = None,
        audio_path: Path | None = None,
        duration_seconds: float | None = None,
        elapsed_seconds: float | None = None,
        processing_backend: str = "",
        error: str = "",
    ) -> None:
        with self._database.write() as connection:
            connection.execute(
                """
                UPDATE job_item_candidates
                SET status=?, raw_audio_path=COALESCE(NULLIF(?, ''), raw_audio_path),
                    audio_path=COALESCE(NULLIF(?, ''), audio_path),
                    duration_seconds=?, elapsed_seconds=?, processing_backend=?,
                    finished_at=?, error=?
                WHERE id=?
                """,
                (
                    status,
                    str(raw_audio_path or ""),
                    str(audio_path or ""),
                    duration_seconds,
                    elapsed_seconds,
                    processing_backend,
                    utc_now(),
                    error,
                    candidate_id,
                ),
            )

    def complete_job_item(self, job_item_id: str) -> dict[str, Any] | None:
        with self._database.write() as connection:
            candidate = connection.execute(
                """
                SELECT * FROM job_item_candidates
                WHERE job_item_id=? AND kind='gpt'
                  AND status='completed' AND audio_path <> ''
                ORDER BY ordinal, submitted_at, id LIMIT 1
                """,
                (job_item_id,),
            ).fetchone()
            if not candidate:
                return None
            connection.execute(
                """
                UPDATE job_items
                SET status='completed', audio_path=?, raw_audio_path=?,
                    duration_seconds=?, elapsed_seconds=?, processing_backend=?,
                    accepted_candidate_id=CASE
                        WHEN accepted_candidate_id='' THEN ?
                        ELSE accepted_candidate_id
                    END,
                    error=''
                WHERE id=?
                """,
                (
                    candidate["audio_path"],
                    candidate["raw_audio_path"],
                    candidate["duration_seconds"],
                    candidate["elapsed_seconds"],
                    candidate["processing_backend"],
                    candidate["id"],
                    job_item_id,
                ),
            )
        return dict(candidate)

    def accept(self, job_item_id: str, candidate_id: str) -> bool:
        with self._database.write() as connection:
            candidate = connection.execute(
                """
                SELECT 1 FROM job_item_candidates
                WHERE id=? AND job_item_id=? AND kind='gpt' AND status='completed'
                  AND audio_path <> ''
                """,
                (candidate_id, job_item_id),
            ).fetchone()
            if not candidate:
                return False
            connection.execute(
                "UPDATE job_items SET accepted_candidate_id=? WHERE id=?",
                (candidate_id, job_item_id),
            )
        return True

    def accepted_items(self, job_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT c.*, ji.sequence, ji.source_line
                FROM job_items ji
                JOIN job_item_candidates c ON c.id=ji.accepted_candidate_id
                WHERE ji.job_id=? AND c.kind='gpt'
                ORDER BY ji.sequence
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def estimated_seconds(self, kind: str) -> float:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT elapsed_seconds FROM job_item_candidates
                WHERE kind=? AND status='completed' AND elapsed_seconds > 0
                ORDER BY finished_at DESC LIMIT 200
                """,
                (kind,),
            ).fetchall()
        samples = sorted(float(row["elapsed_seconds"]) for row in rows)
        if not samples:
            return 18.0 if kind == "gpt" else 1.5
        middle = len(samples) // 2
        if len(samples) % 2:
            return samples[middle]
        return (samples[middle - 1] + samples[middle]) / 2
