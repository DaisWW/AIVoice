from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .database import Database
from .engine_adapter import VoiceEngine
from .legacy_import import LegacyImporter
from .profiles import Profiles
from .queue_worker import JobQueue
from .settings import Settings


EngineFactory = Callable[[Settings, Profiles], Any]


class ApplicationServices:
    """Own application resources without mutating production state at import time."""

    def __init__(
        self,
        settings: Settings,
        engine_factory: EngineFactory = VoiceEngine,
        seed_legacy: bool = True,
    ) -> None:
        self.settings = settings
        self.export_lock = threading.Lock()
        self._engine_factory = engine_factory
        self._seed_legacy = seed_legacy
        self._initialize_lock = threading.Lock()
        self._database: Database | None = None
        self._profiles: Profiles | None = None
        self._engine: Any | None = None
        self._job_queue: JobQueue | None = None

    @property
    def database(self) -> Database:
        if self._database is None:
            raise RuntimeError("Application services are not initialized")
        return self._database

    @property
    def profiles(self) -> Profiles:
        if self._profiles is None:
            raise RuntimeError("Application services are not initialized")
        return self._profiles

    @property
    def engine(self) -> Any:
        if self._engine is None:
            raise RuntimeError("Application services are not initialized")
        return self._engine

    @property
    def job_queue(self) -> JobQueue:
        if self._job_queue is None:
            raise RuntimeError("Application services are not initialized")
        return self._job_queue

    def initialize(self) -> None:
        with self._initialize_lock:
            if self._database is not None:
                return
            self.settings.ensure_directories()
            database = Database(self.settings.database_path)
            database.initialize()
            database.jobs.recover_interrupted()
            database.candidates.recover_interrupted()
            profiles = Profiles.load(self.settings.profiles_path)
            if self._seed_legacy:
                LegacyImporter(database, self.settings.root).run()
            engine = self._engine_factory(self.settings, profiles)
            self._database = database
            self._profiles = profiles
            self._engine = engine
            self._job_queue = JobQueue(self.settings, database, engine)

    def start(self) -> None:
        self.initialize()
        self.job_queue.start()

    def stop(self) -> None:
        if self._job_queue is not None:
            self._job_queue.stop()
