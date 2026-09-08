from __future__ import annotations

from pathlib import Path

from .connection import SQLiteConnection
from .repositories import (
    AuditRepository,
    AuthRepository,
    CandidateRepository,
    ClientRepository,
    ContextRevisionRepository,
    JobRepository,
    LegacyRepository,
    MonitoringRepository,
    ProjectRepository,
    ScriptRepository,
    ScriptLineSelectionRepository,
    VoiceRepository,
    TextGenerationRunRepository,
)
from .schema import initialize_schema


class Database:
    """Composition root for repositories sharing one SQLite database."""

    def __init__(self, path: Path) -> None:
        connection = SQLiteConnection(path)
        self.path = path
        self.clients = ClientRepository(connection)
        self.context_revisions = ContextRevisionRepository(connection)
        self.auth = AuthRepository(connection)
        self.projects = ProjectRepository(connection)
        self.audit = AuditRepository(connection)
        self.voices = VoiceRepository(connection)
        self.scripts = ScriptRepository(connection)
        self.selections = ScriptLineSelectionRepository(connection)
        self.jobs = JobRepository(connection)
        self.text_generation_runs = TextGenerationRunRepository(connection)
        self.candidates = CandidateRepository(connection)
        self.monitoring = MonitoringRepository(connection)
        self.legacy = LegacyRepository(connection)
        self._connection = connection

    def initialize(self) -> None:
        initialize_schema(self._connection)
        self.text_generation_runs.recover_interrupted()
