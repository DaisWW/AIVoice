from __future__ import annotations

import sqlite3

from .connection import SQLiteConnection


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    must_change_password INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    added_by TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY(project_id, user_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id TEXT,
    actor_name TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    project_id TEXT,
    ip_address TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 1,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
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
    reference_text TEXT NOT NULL DEFAULT '',
    quality_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL,
    default_voice_id TEXT,
    default_effect_id TEXT,
    item_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON jobs(submitted_at);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id, sequence);
CREATE INDEX IF NOT EXISTS idx_candidates_item ON job_item_candidates(job_item_id, ordinal, submitted_at);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON job_item_candidates(kind, status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_candidates_queue ON job_item_candidates(origin_type, kind, status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_variants_job_submitted ON job_variants(job_id, submitted_at);
CREATE INDEX IF NOT EXISTS idx_variants_status_submitted ON job_variants(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_variant_items_variant ON job_variant_items(variant_id, sequence);
CREATE INDEX IF NOT EXISTS idx_voice_files_voice ON voice_files(voice_id, enabled);
CREATE INDEX IF NOT EXISTS idx_voices_owner_created ON voices(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scripts_owner_created ON scripts(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_members_user_project ON project_members(user_id, project_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor_created ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_project_created ON audit_logs(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_expiry ON sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
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
        _add_column_if_missing(
            connection,
            "voice_files",
            "reference_text",
            "TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            connection, "voices", "project_id", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "scripts", "project_id", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "jobs", "project_id", "TEXT NOT NULL DEFAULT ''"
        )
        _add_column_if_missing(
            connection, "jobs", "created_by", "TEXT NOT NULL DEFAULT ''"
        )
        _migrate_project_member_roles(connection)
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_project_submitted
                ON jobs(project_id, submitted_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_created_by_submitted
                ON jobs(created_by, submitted_at);
            CREATE INDEX IF NOT EXISTS idx_voices_project_created
                ON voices(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_scripts_project_created
                ON scripts(project_id, created_at);
            """
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


def _migrate_project_member_roles(connection: sqlite3.Connection) -> None:
    """Make projects.owner_id the single source of truth for ownership."""
    connection.execute(
        """
        UPDATE project_members
        SET role=CASE WHEN role='admin' THEN 'admin' ELSE 'member' END
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO project_members(
            project_id, user_id, role, added_by, joined_at
        )
        SELECT id, owner_id, 'owner', owner_id, created_at
        FROM projects
        """
    )
    connection.execute(
        """
        UPDATE project_members
        SET role='owner'
        WHERE EXISTS (
            SELECT 1 FROM projects
            WHERE projects.id=project_members.project_id
              AND projects.owner_id=project_members.user_id
        )
        """
    )


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
