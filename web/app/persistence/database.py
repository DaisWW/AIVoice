from __future__ import annotations

from pathlib import Path

from .connection import SQLiteConnection
from .repositories import (
    CandidateRepository,
    ClientRepository,
    JobRepository,
    LegacyRepository,
    MonitoringRepository,
    ScriptRepository,
    VoiceRepository,
)
from .schema import initialize_schema


class Database:
    """Composition root for repositories sharing one SQLite database."""

    def __init__(self, path: Path) -> None:
        connection = SQLiteConnection(path)
        self.path = path
        self.clients = ClientRepository(connection)
        self.voices = VoiceRepository(connection)
        self.scripts = ScriptRepository(connection)
        self.jobs = JobRepository(connection)
        self.candidates = CandidateRepository(connection)
        self.monitoring = MonitoringRepository(connection)
        self.legacy = LegacyRepository(connection)
        self._connection = connection

    def initialize(self) -> None:
        initialize_schema(self._connection)
