from __future__ import annotations

import time
import threading
from pathlib import Path
from types import SimpleNamespace

from app.database import Database
from app.domain import ScriptItem
from app.queue_worker import JobQueue

from conftest import FakeEngine, make_wav_bytes


def seed_job(database: Database, root: Path, item_count: int = 1) -> str:
    voice_id = database.voices.create("voice", "client", "")
    voice_path = root / f"{voice_id}.wav"
    voice_path.write_bytes(make_wav_bytes())
    database.voices.add_file(
        voice_id, voice_path.name, voice_path, voice_path.stat().st_size
    )
    script_path = root / f"{voice_id}.txt"
    script_path.write_text("mo-la\n", encoding="utf-8")
    items = [
        ScriptItem(index, index, f"text-{index}", "mo-la", "摸啦。", "flat", ())
        for index in range(1, item_count + 1)
    ]
    script_id = database.scripts.create(
        "script", script_path.name, script_path, "client", "test", items
    )
    return database.jobs.create("client", script_id, voice_id, "test_model", items)


def wait_terminal(
    database: Database, job_id: str, timeout: float = 3.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = database.jobs.get(job_id)
        assert job is not None
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {job_id}")


def test_eta_uses_median_generation_elapsed_time(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    database.jobs.finish_item(
        item["id"],
        "completed",
        duration_seconds=120.0,
        elapsed_seconds=7.5,
    )

    assert database.monitoring.estimated_item_seconds() == 7.5


def test_initial_candidates_use_fresh_random_seeds(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()

    first_job = seed_job(database, settings.root)
    second_job = seed_job(database, settings.root)
    first = {row["seed"] for row in database.candidates.list_for_job(first_job)}
    second = {row["seed"] for row in database.candidates.list_for_job(second_job)}

    assert len(first) == 2
    assert len(second) == 2
    assert first.isdisjoint(second)


def test_queue_snapshots_are_batched_in_submission_order(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    jobs = [database.jobs.get(seed_job(database, settings.root)) for _ in range(3)]

    snapshots = database.monitoring.queue_snapshots(
        [job for job in jobs if job is not None]
    )

    assert [snapshots[job["id"]]["queue_position"] for job in jobs] == [1, 2, 3]
    assert [snapshots[job["id"]]["estimated_wait_seconds"] for job in jobs] == [
        0,
        18,
        36,
    ]


def test_running_work_cannot_be_enqueued_twice(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)

    class BlockingEngine(FakeEngine):
        def __init__(self, engine_settings, profiles) -> None:
            super().__init__(engine_settings, profiles)
            self.started = threading.Event()
            self.release = threading.Event()

        def generate(self, *args, **kwargs):
            self.started.set()
            assert self.release.wait(timeout=2)
            return super().generate(*args, **kwargs)

    engine = BlockingEngine(settings, SimpleNamespace())
    worker = JobQueue(settings, database, engine)
    worker.start()
    try:
        assert engine.started.wait(timeout=2)
        worker.submit(job_id)
        assert worker.status()["pending_in_memory"] == 0
        engine.release.set()
        assert wait_terminal(database, job_id)["status"] == "completed"
    finally:
        engine.release.set()
        worker.stop()


def test_queue_failure_isolated_and_pending_items_failed(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    first_job = seed_job(database, settings.root, item_count=2)
    second_job = seed_job(database, settings.root)

    class FailFirstReferenceEngine(FakeEngine):
        def __init__(self, engine_settings, profiles) -> None:
            super().__init__(engine_settings, profiles)
            self.calls = 0

        def prepare_reference(self, voice_files, target_path):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("reference failed")
            return super().prepare_reference(voice_files, target_path)

    engine = FailFirstReferenceEngine(settings, SimpleNamespace())
    worker = JobQueue(settings, database, engine)
    worker.start()
    try:
        first = wait_terminal(database, first_job)
        second = wait_terminal(database, second_job)
        assert first["status"] == "failed"
        assert {item["status"] for item in database.jobs.items(first_job)} == {"failed"}
        assert second["status"] == "completed"
        assert worker.is_running
    finally:
        worker.stop()


def test_unhandled_job_error_does_not_kill_worker(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()

    database = Database(settings.database_path)
    database.initialize()
    first_job = seed_job(database, settings.root)
    second_job = seed_job(database, settings.root)
    original_get = database.jobs.get
    exploded = False

    def exploding_get(job_id: str):
        nonlocal exploded
        if (
            job_id == first_job
            and not exploded
            and threading.current_thread().name == "voice-gpu-worker"
        ):
            exploded = True
            raise RuntimeError("unexpected database read")
        return original_get(job_id)

    database.jobs.get = exploding_get
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))
    worker.start()
    try:
        assert wait_terminal(database, first_job)["status"] == "failed"
        assert wait_terminal(database, second_job)["status"] == "completed"
        assert worker.is_running
    finally:
        worker.stop()


def test_queue_can_restart_and_recover_running_job(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    database.jobs.set_running(job_id)
    item = database.jobs.items(job_id)[0]
    database.jobs.set_item_running(item["id"])

    assert database.jobs.recover_interrupted() == 1
    assert database.jobs.get(job_id)["status"] == "queued"
    assert database.jobs.items(job_id)[0]["status"] == "queued"

    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))
    worker.start()
    worker.stop()
    worker.start()
    try:
        assert wait_terminal(database, job_id)["status"] == "completed"
    finally:
        worker.stop()
