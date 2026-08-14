from __future__ import annotations

import sqlite3

from .connection import SQLiteConnection


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_files (
    id TEXT PRIMARY KEY,
    voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE RESTRICT,
    original_name TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    emotion_tag TEXT NOT NULL DEFAULT 'neutral',
    quality_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    default_voice_id TEXT,
    default_effect_id TEXT,
    item_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE RESTRICT,
    voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE RESTRICT,
    model_id TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    effect_settings_json TEXT NOT NULL DEFAULT '{}',
    candidate_count INTEGER NOT NULL DEFAULT 1,
    reference_emotion TEXT NOT NULL DEFAULT 'all',
    status TEXT NOT NULL,
    total_items INTEGER NOT NULL,
    completed_items INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    eta_seconds INTEGER,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_items (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    source_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    pronunciation TEXT NOT NULL,
    generated_text TEXT NOT NULL,
    direction TEXT NOT NULL,
    emphasis TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    audio_path TEXT NOT NULL DEFAULT '',
    raw_audio_path TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    elapsed_seconds REAL,
    processing_backend TEXT NOT NULL DEFAULT '',
    accepted_candidate_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(job_id, sequence)
);

CREATE TABLE IF NOT EXISTS job_item_candidates (
    id TEXT PRIMARY KEY,
    job_item_id TEXT NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,
    source_candidate_id TEXT REFERENCES job_item_candidates(id) ON DELETE SET NULL,
    origin_type TEXT NOT NULL,
    origin_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'simple',
    ordinal INTEGER NOT NULL,
    seed INTEGER,
    text TEXT NOT NULL,
    pronunciation TEXT NOT NULL,
    generated_text TEXT NOT NULL,
    direction TEXT NOT NULL,
    emphasis TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    generation_settings_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    raw_audio_path TEXT NOT NULL DEFAULT '',
    audio_path TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    elapsed_seconds REAL,
    processing_backend TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(origin_type, origin_id)
);

CREATE TABLE IF NOT EXISTS job_variants (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_variant_id TEXT REFERENCES job_variants(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    status TEXT NOT NULL,
    total_items INTEGER NOT NULL,
    completed_items INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    eta_seconds INTEGER,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_variant_items (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES job_variants(id) ON DELETE CASCADE,
    job_item_id TEXT NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL,
    audio_path TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    elapsed_seconds REAL,
    processing_backend TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(variant_id, job_item_id),
    UNIQUE(variant_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_submitted ON jobs(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_client_submitted ON jobs(client_id, submitted_at);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id, sequence);
CREATE INDEX IF NOT EXISTS idx_candidates_item ON job_item_candidates(job_item_id, ordinal, submitted_at);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON job_item_candidates(kind, status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_variants_job_submitted ON job_variants(job_id, submitted_at);
CREATE INDEX IF NOT EXISTS idx_variants_status_submitted ON job_variants(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_variant_items_variant ON job_variant_items(variant_id, sequence);
CREATE INDEX IF NOT EXISTS idx_voice_files_voice ON voice_files(voice_id, enabled);
"""


def initialize_schema(database: SQLiteConnection) -> None:
    database.path.parent.mkdir(parents=True, exist_ok=True)
    with database.write() as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA journal_mode = WAL")
        _add_column_if_missing(connection, "job_items", "elapsed_seconds", "REAL")
        _add_column_if_missing(
            connection,
            "jobs",
            "display_name",
            "TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            connection,
            "jobs",
            "effect_settings_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        _add_column_if_missing(
            connection,
            "jobs",
            "candidate_count",
            "INTEGER NOT NULL DEFAULT 1",
        )
        _add_column_if_missing(
            connection,
            "jobs",
            "reference_emotion",
            "TEXT NOT NULL DEFAULT 'all'",
        )
        _add_column_if_missing(
            connection,
            "job_items",
            "accepted_candidate_id",
            "TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            connection,
            "voice_files",
            "emotion_tag",
            "TEXT NOT NULL DEFAULT 'neutral'",
        )
        _add_column_if_missing(
            connection,
            "voice_files",
            "quality_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        _backfill_candidates(connection)


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _backfill_candidates(connection: sqlite3.Connection) -> None:
    """Expose existing initial and full-version audio through the candidate model."""
    _backfill_initial_candidates(connection)
    _backfill_legacy_variants(connection)


def _backfill_initial_candidates(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO job_item_candidates(
            id, job_item_id, source_candidate_id, origin_type, origin_id, kind,
            name, mode, ordinal, seed, text, pronunciation, generated_text,
            direction, emphasis, settings_json, status, raw_audio_path,
            audio_path, duration_seconds, elapsed_seconds, processing_backend,
            submitted_at, started_at, finished_at, error
        )
        SELECT
            'candidate-initial-' || ji.id,
            ji.id,
            NULL,
            'job_item',
            ji.id,
            'gpt',
            '初始候选',
            'initial',
            1,
            NULL,
            ji.text,
            ji.pronunciation,
            ji.generated_text,
            ji.direction,
            ji.emphasis,
            j.effect_settings_json,
            ji.status,
            ji.raw_audio_path,
            ji.audio_path,
            ji.duration_seconds,
            ji.elapsed_seconds,
            ji.processing_backend,
            j.submitted_at,
            j.started_at,
            j.finished_at,
            ji.error
        FROM job_items ji
        JOIN jobs j ON j.id=ji.job_id
        """
    )
    connection.execute(
        """
        UPDATE job_items
        SET accepted_candidate_id=(
            SELECT c.id FROM job_item_candidates c
            WHERE c.origin_type='job_item' AND c.origin_id=job_items.id
        )
        WHERE accepted_candidate_id=''
          AND EXISTS(
            SELECT 1 FROM job_item_candidates c
            WHERE c.origin_type='job_item' AND c.origin_id=job_items.id
              AND c.status='completed' AND c.audio_path <> ''
          )
        """
    )


def _backfill_legacy_variants(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO job_item_candidates(
            id, job_item_id, source_candidate_id, origin_type, origin_id, kind,
            name, mode, ordinal, seed, text, pronunciation, generated_text,
            direction, emphasis, settings_json, status, raw_audio_path,
            audio_path, duration_seconds, elapsed_seconds, processing_backend,
            submitted_at, started_at, finished_at, error
        )
        SELECT
            'candidate-' || vi.id,
            vi.job_item_id,
            (
                SELECT c.id FROM job_item_candidates c
                WHERE c.origin_type='job_item' AND c.origin_id=vi.job_item_id
            ),
            'variant_item',
            vi.id,
            'dsp',
            jv.name,
            jv.mode,
            1000 + vi.rowid,
            NULL,
            ji.text,
            ji.pronunciation,
            ji.generated_text,
            ji.direction,
            ji.emphasis,
            jv.settings_json,
            vi.status,
            ji.raw_audio_path,
            vi.audio_path,
            vi.duration_seconds,
            vi.elapsed_seconds,
            vi.processing_backend,
            jv.submitted_at,
            jv.started_at,
            jv.finished_at,
            vi.error
        FROM job_variant_items vi
        JOIN job_variants jv ON jv.id=vi.variant_id
        JOIN job_items ji ON ji.id=vi.job_item_id
        WHERE vi.audio_path <> ''
        """
    )
