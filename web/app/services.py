from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .auth import AuthService
from .database import Database
from .engine_adapter import VoiceEngine
from .legacy_import import LegacyImporter
from .profiles import Profiles
from .provider_config import ProviderConfigStore
from .queue_worker import JobQueue
from .settings import Settings
from .storage import cleanup_download_snapshots
from .text_generation import TextGenerationService, TextModelConfigStore
from .text_generation_runner import TextGenerationRunner


EngineFactory = Callable[[Settings, Profiles, ProviderConfigStore], Any]


class ApplicationServices:
    """Own application resources without mutating production state at import time."""

    def __init__(
        self,
        settings: Settings,
        engine_factory: EngineFactory = VoiceEngine,
        seed_legacy: bool = True,
    ) -> None:
        self.settings = settings
        self.export_lock = threading.RLock()
        self.script_write_lock = threading.RLock()
        self.job_mutation_lock = threading.RLock()
        self._engine_factory = engine_factory
        self._seed_legacy = seed_legacy
        self._initialize_lock = threading.Lock()
        self._database: Database | None = None
        self._auth: AuthService | None = None
        self._profiles: Profiles | None = None
        self._provider_config: ProviderConfigStore | None = None
        self._engine: Any | None = None
        self._job_queue: JobQueue | None = None
        self._text_generation: TextGenerationService | None = None
        self._text_generation_runner: TextGenerationRunner | None = None

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
    def auth(self) -> AuthService:
        if self._auth is None:
            raise RuntimeError("Application services are not initialized")
        return self._auth

    @property
    def engine(self) -> Any:
        if self._engine is None:
            raise RuntimeError("Application services are not initialized")
        return self._engine

    @property
    def provider_config(self) -> ProviderConfigStore:
        if self._provider_config is None:
            raise RuntimeError("Application services are not initialized")
        return self._provider_config

    @property
    def job_queue(self) -> JobQueue:
        if self._job_queue is None:
            raise RuntimeError("Application services are not initialized")
        return self._job_queue

    @property
    def text_generation(self) -> TextGenerationService:
        if self._text_generation is None:
            raise RuntimeError("Application services are not initialized")
        return self._text_generation

    @property
    def text_generation_runner(self) -> TextGenerationRunner:
        if self._text_generation_runner is None:
            raise RuntimeError("Application services are not initialized")
        return self._text_generation_runner

    def initialize(self) -> None:
        with self._initialize_lock:
            if self._database is not None:
                return
            self.settings.ensure_directories()
            cleanup_download_snapshots(self.settings.export_root)
            database = Database(self.settings.database_path)
            database.initialize()
            auth = AuthService(database, self.settings.data_root)
            admin = auth.ensure_bootstrap_admin()
            database.projects.ensure_legacy_project(str(admin["id"]))
            database.jobs.recover_interrupted()
            database.candidates.recover_interrupted()
            profiles = Profiles.load(self.settings.profiles_path)
            provider_config = ProviderConfigStore(self.settings.provider_config_path)
            text_generation = TextGenerationService(
                TextModelConfigStore(self.settings.text_model_config_path)
            )
            if self._seed_legacy:
                LegacyImporter(database, self.settings.root).run()
                database.projects.ensure_legacy_project(str(admin["id"]))
            engine = self._engine_factory(self.settings, profiles, provider_config)
            self._database = database
            self._auth = auth
            self._profiles = profiles
            self._provider_config = provider_config
            self._text_generation = text_generation
            self._text_generation_runner = TextGenerationRunner(
                database, text_generation
            )
            self._engine = engine
            self._job_queue = JobQueue(self.settings, database, engine)

    def start(self) -> None:
        self.initialize()
        self.job_queue.start()

    def stop(self) -> None:
        if self._job_queue is not None:
            self._job_queue.stop()
        if self._text_generation_runner is not None:
            self._text_generation_runner.stop()
