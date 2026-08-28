from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from ...domain import ScriptItem
from ...storage import ensure_within, resolve_audio_path
from ...value_utils import stored_float
from ..common import utc_now
from ..connection import SQLiteConnection


class _CompletionConflict(Exception):
    """The candidate lease changed before its output could be committed."""


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

    def list_for_script(self, script_id: str) -> list[dict[str, Any]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT c.*, ji.job_id, ji.sequence, ji.accepted_candidate_id,
                       j.script_id, j.voice_id, j.model_id, j.submitted_at,
                       v.name AS voice_name
                FROM job_item_candidates c
                JOIN job_items ji ON ji.id=c.job_item_id
                JOIN jobs j ON j.id=ji.job_id
                JOIN voices v ON v.id=j.voice_id
                WHERE j.script_id=? AND c.kind='gpt'
                ORDER BY ji.sequence, j.submitted_at DESC, c.ordinal, c.submitted_at, c.id
                """,
                (script_id,),
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
        return [str(row["id"]) for row in self.queued_manual_entries(kind)]

    def queued_manual_entries(self, kind: str) -> list[dict[str, str]]:
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT id, submitted_at FROM job_item_candidates
                WHERE origin_type='manual' AND kind=? AND status='queued'
                ORDER BY submitted_at, id
                """,
                (kind,),
            ).fetchall()
        return [
            {"id": str(row["id"]), "submitted_at": str(row["submitted_at"] or "")}
            for row in rows
        ]

    def recover_interrupted(self) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='queued', started_at=NULL,
                    run_token='',
                    error='服务器重启后重新排队'
                WHERE kind='gpt' AND status='running'
                """
            )
        return int(cursor.rowcount)

    def set_running(self, candidate_id: str) -> str | None:
        """Claim a queued candidate and return its execution lease token."""
        token = uuid.uuid4().hex
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='running', started_at=?, run_token=?, error=''
                WHERE id=? AND status='queued'
                """,
                (utc_now(), token, candidate_id),
            )
        return token if cursor.rowcount == 1 else None

    def claim_running(self, candidate_id: str) -> str | None:
        """Explicit alias for callers that want to make the lease boundary clear."""
        return self.set_running(candidate_id)

    def requeue_if_token(
        self, candidate_id: str, token: str, message: str = ""
    ) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='queued', started_at=NULL, run_token='', error=?
                WHERE id=? AND status='running' AND run_token=?
                """,
                (message, candidate_id, token),
            )
        return cursor.rowcount == 1

    def fail_if_token(self, candidate_id: str, token: str, error: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='failed', finished_at=?, run_token='', error=?,
                    raw_audio_path='', audio_path='', duration_seconds=NULL,
                    elapsed_seconds=NULL, processing_backend=''
                WHERE id=? AND status='running' AND run_token=?
                """,
                (utc_now(), error, candidate_id, token),
            )
        return cursor.rowcount == 1

    def fail_queued(self, candidate_id: str, error: str) -> bool:
        """Fail only a still-queued candidate after an unclaimed error."""
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='failed', finished_at=?, run_token='', error=?,
                    raw_audio_path='', audio_path='', duration_seconds=NULL,
                    elapsed_seconds=NULL, processing_backend=''
                WHERE id=? AND status='queued'
                """,
                (utc_now(), error, candidate_id),
            )
        return cursor.rowcount == 1

    def complete_if_token(
        self,
        candidate_id: str,
        token: str,
        temporary_path: Path,
        final_path: Path,
        allowed_root: Path,
        duration_seconds: float,
        elapsed_seconds: float,
        processing_backend: str,
    ) -> bool:
        """Publish one generated file only while the candidate lease is current."""
        temporary = ensure_within(temporary_path, allowed_root)
        final = ensure_within(final_path, allowed_root)
        if temporary == final or not temporary.is_file():
            return False
        backup: Path | None = None
        published = False
        try:
            with self._database.write() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT 1 FROM job_item_candidates
                    WHERE id=? AND status='running' AND run_token=?
                    """,
                    (candidate_id, token),
                ).fetchone()
                if not row:
                    return False
                final.parent.mkdir(parents=True, exist_ok=True)
                backup = final.with_name(f".{final.name}.{uuid.uuid4().hex}.bak")
                if final.exists() or final.is_symlink():
                    os.replace(final, backup)
                os.replace(temporary, final)
                published = True
                if (
                    self._mark_completed(
                        connection,
                        candidate_id,
                        token,
                        final,
                        duration_seconds,
                        elapsed_seconds,
                        processing_backend,
                    )
                    != 1
                ):
                    raise _CompletionConflict
        except _CompletionConflict:
            self._restore_publication(temporary, final, backup, published)
            return False
        except Exception:
            self._restore_publication(temporary, final, backup, published)
            raise
        try:
            if backup is not None:
                backup.unlink(missing_ok=True)
        except OSError:
            # The generated output is already durable; a stale backup is harmless.
            pass
        return True

    @staticmethod
    def _mark_completed(
        connection: Any,
        candidate_id: str,
        token: str,
        final: Path,
        duration_seconds: float,
        elapsed_seconds: float,
        processing_backend: str,
    ) -> int:
        cursor = connection.execute(
            """
            UPDATE job_item_candidates
            SET status='completed', raw_audio_path=?, audio_path=?,
                duration_seconds=?, elapsed_seconds=?, processing_backend=?,
                finished_at=?, run_token='', error=''
            WHERE id=? AND status='running' AND run_token=?
            """,
            (
                str(final),
                str(final),
                duration_seconds,
                elapsed_seconds,
                processing_backend,
                utc_now(),
                candidate_id,
                token,
            ),
        )
        return int(cursor.rowcount)

    @staticmethod
    def _restore_publication(
        temporary: Path,
        final: Path,
        backup: Path | None,
        published: bool,
    ) -> None:
        if published and (final.exists() or final.is_symlink()):
            os.replace(final, temporary)
        if backup is not None and (backup.exists() or backup.is_symlink()):
            os.replace(backup, final)

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
            if status == "failed":
                connection.execute(
                    """
                    UPDATE job_item_candidates
                    SET status='failed', raw_audio_path='', audio_path='',
                        duration_seconds=NULL, elapsed_seconds=NULL,
                        processing_backend='', finished_at=?, run_token='', error=?
                    WHERE id=?
                    """,
                    (utc_now(), error, candidate_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE job_item_candidates
                    SET status=?, raw_audio_path=COALESCE(NULLIF(?, ''), raw_audio_path),
                        audio_path=COALESCE(NULLIF(?, ''), audio_path),
                        duration_seconds=?, elapsed_seconds=?, processing_backend=?,
                        finished_at=?, run_token='', error=?
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

    def fail_active(self, candidate_id: str, error: str) -> bool:
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='failed', finished_at=?, error=?,
                    raw_audio_path='', audio_path='', duration_seconds=NULL,
                    elapsed_seconds=NULL, processing_backend='', run_token=''
                WHERE id=? AND status IN ('queued', 'running')
                """,
                (utc_now(), error, candidate_id),
            )
        return cursor.rowcount == 1

    def requeue_missing_output(self, candidate_id: str) -> bool:
        """Requeue a completed candidate after its output file disappears."""
        with self._database.write() as connection:
            cursor = connection.execute(
                """
                UPDATE job_item_candidates
                SET status='queued', started_at=NULL, finished_at=NULL,
                    raw_audio_path='', audio_path='', duration_seconds=NULL,
                    elapsed_seconds=NULL, processing_backend='', run_token='', error=''
                WHERE id=? AND status='completed'
                """,
                (candidate_id,),
            )
        return cursor.rowcount == 1

    def complete_job_item(
        self,
        job_item_id: str,
        allowed_root: Path,
        item_token: str | None = None,
        expected_item_status: str | None = None,
    ) -> dict[str, Any] | None:
        """Promote a completed candidate without losing an explicit selection."""
        with self._database.write() as connection:
            item_query = (
                "SELECT accepted_candidate_id, status FROM job_items WHERE id=?"
            )
            item_parameters: tuple[Any, ...] = (job_item_id,)
            if item_token is not None:
                item_query += " AND status='running' AND run_token=?"
                item_parameters += (item_token,)
            item = connection.execute(item_query, item_parameters).fetchone()
            if not item:
                return None
            if (
                expected_item_status is not None
                and str(item["status"] or "") != expected_item_status
            ):
                return None
            candidates = connection.execute(
                """
                SELECT * FROM job_item_candidates
                WHERE job_item_id=? AND kind='gpt'
                  AND status='completed'
                ORDER BY ordinal, submitted_at, id
                """,
                (job_item_id,),
            ).fetchall()
            accepted_id = str(item["accepted_candidate_id"] or "")
            ordered = (
                [row for row in candidates if str(row["id"]) == accepted_id]
                if accepted_id
                else candidates
            )
            candidate = None
            safe_audio: Path | None = None
            safe_raw: Path | None = None
            for row in ordered:
                paths = self._validated_output_paths(row, allowed_root)
                if paths is None:
                    continue
                candidate = row
                safe_audio, safe_raw = paths
                break
            if not candidate or safe_audio is None:
                return None
            promoted = dict(candidate)
            promoted["audio_path"] = str(safe_audio)
            promoted["raw_audio_path"] = str(safe_raw or safe_audio)
            update_where = "WHERE id=?"
            update_parameters: list[Any] = [
                promoted["audio_path"],
                promoted["raw_audio_path"],
                candidate["duration_seconds"],
                candidate["elapsed_seconds"],
                candidate["processing_backend"],
                candidate["id"],
                job_item_id,
            ]
            if item_token is not None:
                update_where += " AND status='running' AND run_token=?"
                update_parameters.append(item_token)
            elif expected_item_status is not None:
                update_where += " AND status=?"
                update_parameters.append(expected_item_status)
            cursor = connection.execute(
                f"""
                UPDATE job_items
                SET status='completed', audio_path=?, raw_audio_path=?,
                    duration_seconds=?, elapsed_seconds=?, processing_backend=?,
                    run_token='',
                    accepted_candidate_id=CASE
                        WHEN COALESCE(accepted_candidate_id, '')='' THEN ?
                        ELSE accepted_candidate_id
                    END,
                    error=''
                {update_where}
                """,
                tuple(update_parameters),
            )
            if cursor.rowcount != 1:
                return None
        return promoted

    @staticmethod
    def _validated_output_paths(
        candidate: Any, allowed_root: Path
    ) -> tuple[Path, Path | None] | None:
        audio = resolve_audio_path(candidate, allowed_root)
        if audio is None:
            return None
        raw_value = str(candidate["raw_audio_path"] or "")
        raw = None
        if raw_value:
            try:
                raw = ensure_within(Path(raw_value), allowed_root)
            except (OSError, RuntimeError, TypeError, ValueError):
                raw = None
            if raw is not None and not raw.is_file():
                raw = None
        return audio, raw

    def accept(self, job_item_id: str, candidate_id: str) -> bool:
        with self._database.write() as connection:
            candidate = connection.execute(
                """
                SELECT 1 FROM job_item_candidates
                WHERE id=? AND job_item_id=? AND kind='gpt' AND status='completed'
                  AND (audio_path <> '' OR raw_audio_path <> '')
                """,
                (candidate_id, job_item_id),
            ).fetchone()
            if not candidate:
                return False
            connection.execute(
                """
                UPDATE job_items
                SET accepted_candidate_id=?, status='completed',
                    audio_path=(SELECT audio_path FROM job_item_candidates WHERE id=?),
                    raw_audio_path=(SELECT raw_audio_path FROM job_item_candidates WHERE id=?),
                    duration_seconds=(SELECT duration_seconds FROM job_item_candidates WHERE id=?),
                    elapsed_seconds=(SELECT elapsed_seconds FROM job_item_candidates WHERE id=?),
                    processing_backend=(SELECT processing_backend FROM job_item_candidates WHERE id=?),
                    error=''
                WHERE id=?
                """,
                (
                    candidate_id,
                    candidate_id,
                    candidate_id,
                    candidate_id,
                    candidate_id,
                    candidate_id,
                    job_item_id,
                ),
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
        samples = sorted(
            value
            for row in rows
            if (value := stored_float(row["elapsed_seconds"], 0.0, minimum=0)) > 0
        )
        if not samples:
            return 18.0 if kind == "gpt" else 1.5
        middle = len(samples) // 2
        if len(samples) % 2:
            return samples[middle]
        return (samples[middle - 1] + samples[middle]) / 2
