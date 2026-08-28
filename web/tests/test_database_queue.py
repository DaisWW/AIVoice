from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import app.queue_worker as queue_worker_module
import pytest

from app.database import Database
from app.domain import ScriptItem
from app.job_processor import JobProcessor
from app.queue_worker import JobQueue, QueueWork

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


def test_queue_snapshot_counts_manual_candidates_ahead_of_jobs(
    settings_factory,
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source_job_id = seed_job(database, settings.root)
    source_item = database.jobs.items(source_job_id)[0]
    database.jobs.finish_item(source_item["id"], "completed")
    database.jobs.finish(source_job_id, "completed")
    candidate_id = database.candidates.create_regeneration(
        source_item,
        ScriptItem(1, 1, "manual", "mo-la", "摸啦。", "flat", ()),
        1,
        {},
    )
    queued_job = database.jobs.get(seed_job(database, settings.root))
    assert queued_job is not None

    queued = database.monitoring.queue_snapshots([queued_job])[queued_job["id"]]

    assert queued["queue_position"] == 2
    assert queued["estimated_wait_seconds"] == 18

    database.candidates.set_running(candidate_id)
    running = database.monitoring.queue_snapshots([queued_job])[queued_job["id"]]

    assert running["queue_position"] == 1
    assert running["estimated_wait_seconds"] == 18


def test_candidate_claim_is_atomic(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source_job_id = seed_job(database, settings.root)
    source_item = database.jobs.items(source_job_id)[0]
    database.jobs.finish_item(source_item["id"], "completed")
    database.jobs.finish(source_job_id, "completed")
    candidate_id = database.candidates.create_regeneration(
        source_item,
        ScriptItem(1, 1, "manual", "mo-la", "摸啦。", "flat", ()),
        1,
        {},
    )

    assert database.candidates.set_running(candidate_id)
    assert not database.candidates.set_running(candidate_id)


def test_stale_candidate_lease_cannot_publish_output(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    first_token = database.candidates.set_running(candidate["id"])
    assert first_token
    assert database.candidates.requeue_if_token(candidate["id"], first_token)
    second_token = database.candidates.set_running(candidate["id"])
    assert second_token and second_token != first_token

    temporary = settings.root / "stale.wav"
    final = settings.root / "published.wav"
    temporary.write_bytes(make_wav_bytes())

    assert not database.candidates.complete_if_token(
        candidate["id"],
        first_token,
        temporary,
        final,
        settings.root,
        0.08,
        0.2,
        "fake",
    )
    assert temporary.is_file()
    assert not final.exists()
    assert database.candidates.get(candidate["id"])["run_token"] == second_token


def test_candidate_completion_restores_file_when_database_commit_fails(
    settings_factory, monkeypatch
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    token = database.candidates.set_running(candidate["id"])
    assert token

    temporary = settings.root / "new.wav"
    final = settings.root / "published.wav"
    temporary.write_bytes(b"new output")
    final.write_bytes(b"previous output")

    def fail_mark(*args: object, **kwargs: object) -> int:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(database.candidates, "_mark_completed", fail_mark)

    with pytest.raises(RuntimeError, match="database unavailable"):
        database.candidates.complete_if_token(
            candidate["id"],
            token,
            temporary,
            final,
            settings.root,
            0.08,
            0.2,
            "fake",
        )

    assert temporary.read_bytes() == b"new output"
    assert final.read_bytes() == b"previous output"
    assert not list(settings.root.glob(".*.bak"))
    recovered = database.candidates.get(candidate["id"])
    assert recovered is not None
    assert recovered["status"] == "running"
    assert recovered["run_token"] == token


def test_candidate_completion_preserves_concurrent_acceptance(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidates = database.candidates.list_for_item(item["id"])
    paths: list[Path] = []
    for candidate in candidates:
        path = settings.root / f"{candidate['id']}.wav"
        path.write_bytes(make_wav_bytes())
        paths.append(path)
        database.candidates.finish(
            str(candidate["id"]),
            "completed",
            raw_audio_path=path,
            audio_path=path,
            duration_seconds=0.08,
            elapsed_seconds=0.2,
            processing_backend="fake",
        )

    accepted_id = str(candidates[1]["id"])
    assert database.candidates.accept(str(item["id"]), accepted_id)
    promoted = database.candidates.complete_job_item(str(item["id"]), settings.root)

    assert promoted is not None
    assert promoted["id"] == accepted_id
    refreshed = database.jobs.item(job_id, str(item["id"]))
    assert refreshed is not None
    assert refreshed["accepted_candidate_id"] == accepted_id
    assert refreshed["audio_path"] == str(paths[1].resolve())


def test_explicit_acceptance_does_not_fall_back_when_audio_is_missing(
    settings_factory,
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidates = database.candidates.list_for_item(item["id"])
    paths: list[Path] = []
    for candidate in candidates:
        path = settings.root / f"{candidate['id']}.wav"
        path.write_bytes(make_wav_bytes())
        paths.append(path)
        database.candidates.finish(
            str(candidate["id"]),
            "completed",
            raw_audio_path=path,
            audio_path=path,
            duration_seconds=0.08,
            elapsed_seconds=0.2,
            processing_backend="fake",
        )

    accepted_id = str(candidates[1]["id"])
    assert database.candidates.accept(str(item["id"]), accepted_id)
    before = database.jobs.item(job_id, str(item["id"]))
    assert before is not None
    paths[1].unlink()

    promoted = database.candidates.complete_job_item(str(item["id"]), settings.root)

    assert promoted is None
    after = database.jobs.item(job_id, str(item["id"]))
    assert after is not None
    assert after["accepted_candidate_id"] == accepted_id
    assert after["status"] == before["status"] == "completed"
    assert after["audio_path"] == before["audio_path"] == str(paths[1].resolve())


def test_raw_only_candidate_can_be_accepted_and_promoted(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    raw_path = settings.root / "legacy-raw.wav"
    raw_path.write_bytes(make_wav_bytes())
    database.candidates.finish(
        str(candidate["id"]),
        "completed",
        raw_audio_path=raw_path,
        audio_path=None,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="legacy",
    )

    assert database.candidates.accept(str(item["id"]), str(candidate["id"]))
    promoted = database.candidates.complete_job_item(str(item["id"]), settings.root)

    assert promoted is not None
    assert promoted["audio_path"] == str(raw_path.resolve())
    refreshed = database.jobs.item(job_id, str(item["id"]))
    assert refreshed is not None
    assert refreshed["accepted_candidate_id"] == candidate["id"]
    assert refreshed["audio_path"] == str(raw_path.resolve())


def test_raw_only_completed_item_is_not_regenerated(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    raw_path = settings.root / "legacy-item-raw.wav"
    raw_path.write_bytes(make_wav_bytes())
    database.jobs.finish_item(
        str(item["id"]),
        "completed",
        raw_audio_path=raw_path,
        audio_path=None,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="legacy",
    )
    database.jobs.finish(job_id, "queued")

    engine = FakeEngine(settings, SimpleNamespace())
    JobProcessor(settings, database, engine, threading.Event()).process_job(job_id)

    assert engine.generate_calls == 0
    assert database.jobs.get(job_id)["status"] == "completed"


def test_promoting_candidate_releases_item_lease(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    output = settings.root / "candidate.wav"
    output.write_bytes(make_wav_bytes())
    database.candidates.finish(
        str(candidate["id"]),
        "completed",
        raw_audio_path=output,
        audio_path=output,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="fake",
    )
    item_token = database.jobs.set_item_running(item["id"])
    assert item_token

    promoted = database.candidates.complete_job_item(
        str(item["id"]), settings.root, item_token=item_token
    )

    assert promoted is not None
    refreshed = database.jobs.item(job_id, str(item["id"]))
    assert refreshed is not None
    assert refreshed["status"] == "completed"
    assert refreshed["run_token"] == ""


def test_candidate_completion_rejects_audio_outside_workspace(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidates = database.candidates.list_for_item(item["id"])
    outside = settings.root.parent / "outside-candidate.wav"
    outside.write_bytes(make_wav_bytes())
    database.candidates.finish(
        str(candidates[0]["id"]),
        "completed",
        raw_audio_path=outside,
        audio_path=outside,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="fake",
    )
    inside = settings.root / "inside-candidate.wav"
    inside.write_bytes(make_wav_bytes())
    database.candidates.finish(
        str(candidates[1]["id"]),
        "completed",
        raw_audio_path=inside,
        audio_path=inside,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="fake",
    )

    promoted = database.candidates.complete_job_item(str(item["id"]), settings.root)

    assert promoted is not None
    assert promoted["id"] == candidates[1]["id"]
    assert database.jobs.item(job_id, str(item["id"]))["audio_path"] == str(
        inside.resolve()
    )


def test_manual_candidate_does_not_promote_changed_item(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    output = settings.root / "candidate.wav"
    output.write_bytes(make_wav_bytes())
    database.candidates.finish(
        str(candidate["id"]),
        "completed",
        raw_audio_path=output,
        audio_path=output,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="fake",
    )
    database.jobs.finish_item(item["id"], "failed", error="已被其他操作标记失败")

    promoted = database.candidates.complete_job_item(
        str(item["id"]),
        settings.root,
        expected_item_status="queued",
    )

    assert promoted is None
    refreshed = database.jobs.item(job_id, str(item["id"]))
    assert refreshed is not None
    assert refreshed["status"] == "failed"


def test_manual_candidate_does_not_overwrite_status_changed_after_read(
    settings_factory, monkeypatch
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    output = settings.root / "candidate.wav"
    output.write_bytes(make_wav_bytes())
    database.candidates.finish(
        str(candidate["id"]),
        "completed",
        raw_audio_path=output,
        audio_path=output,
        duration_seconds=0.08,
        elapsed_seconds=0.2,
        processing_backend="fake",
    )
    original_validate = database.candidates._validated_output_paths

    def change_item_status(candidate_row, allowed_root):
        database.jobs.finish_item(item["id"], "failed", error="状态已变化")
        return original_validate(candidate_row, allowed_root)

    monkeypatch.setattr(
        database.candidates, "_validated_output_paths", change_item_status
    )

    promoted = database.candidates.complete_job_item(
        str(item["id"]),
        settings.root,
        expected_item_status="queued",
    )

    assert promoted is None
    refreshed = database.jobs.item(job_id, str(item["id"]))
    assert refreshed is not None
    assert refreshed["status"] == "failed"


def test_manual_candidate_reference_error_is_sanitized(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source_job_id = seed_job(database, settings.root)
    source_item = database.jobs.items(source_job_id)[0]
    database.jobs.finish_item(source_item["id"], "completed")
    database.jobs.finish(source_job_id, "completed")
    candidate_id = database.candidates.create_regeneration(
        source_item,
        ScriptItem(1, 1, "manual", "mo-la", "摸啦。", "flat", ()),
        1,
        {},
    )

    class LeakyReferenceEngine(FakeEngine):
        def prepare_reference(self, *args, **kwargs):
            raise RuntimeError(r"failed C:\\private\\api_key=secret")

    processor = JobProcessor(
        settings,
        database,
        LeakyReferenceEngine(settings, SimpleNamespace()),
        threading.Event(),
    )
    processor.process_candidate(candidate_id)

    candidate = database.candidates.get(candidate_id)
    assert candidate is not None
    assert candidate["status"] == "failed"
    assert candidate["error"] == "参考音频准备失败: 无法准备参考音频"


def test_stale_candidate_is_not_sent_to_engine(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source_job_id = seed_job(database, settings.root)
    source_item = database.jobs.items(source_job_id)[0]
    database.jobs.finish_item(source_item["id"], "completed")
    database.jobs.finish(source_job_id, "completed")
    candidate_id = database.candidates.create_regeneration(
        source_item,
        ScriptItem(1, 1, "manual", "mo-la", "摸啦。", "flat", ()),
        1,
        {},
    )
    database.candidates.set_running = lambda _: False
    engine = FakeEngine(settings, SimpleNamespace())
    JobProcessor(settings, database, engine, threading.Event()).process_candidate(
        candidate_id
    )

    assert engine.generate_calls == 0


def test_generation_failure_removes_partial_output(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)

    class PartialOutputEngine(FakeEngine):
        def generate(self, *args, **kwargs):
            output_path = kwargs["output_path"]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"partial")
            raise RuntimeError("generation failed")

    processor = JobProcessor(
        settings,
        database,
        PartialOutputEngine(settings, SimpleNamespace()),
        threading.Event(),
    )
    processor.process_job(job_id)

    assert database.jobs.get(job_id)["status"] == "failed"
    assert not (settings.job_root / job_id / "audio" / "001.wav").exists()


def test_reference_failure_removes_partial_reference(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)

    class PartialReferenceEngine(FakeEngine):
        def prepare_reference(self, voice_files, target_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"partial")
            raise RuntimeError("reference failed")

    processor = JobProcessor(
        settings,
        database,
        PartialReferenceEngine(settings, SimpleNamespace()),
        threading.Event(),
    )
    processor.process_job(job_id)

    assert database.jobs.get(job_id)["status"] == "failed"
    assert not (settings.job_root / job_id / "reference.wav").exists()


def test_refresh_summary_clears_recovered_job_error(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    database.jobs.finish(job_id, "failed", "旧失败信息")
    database.jobs.finish_item(item["id"], "completed")

    database.jobs.refresh_summary(job_id)

    recovered = database.jobs.get(job_id)
    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["error"] == ""


def test_missing_reference_snapshot_fails_with_clear_error(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    job = database.jobs.get(job_id)
    assert job is not None
    snapshot = json.loads(job["reference_files_json"])
    assert snapshot
    Path(snapshot[0]["source_path"]).unlink()
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))

    worker.start()
    try:
        failed = wait_terminal(database, job_id)
    finally:
        worker.stop()

    assert failed["status"] == "failed"
    assert "参考录音快照已不存在" in failed["error"]


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


def test_queue_replays_work_submitted_before_start(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))

    worker.submit(job_id)
    assert worker.status()["pending_in_memory"] == 1

    worker.start()
    try:
        assert wait_terminal(database, job_id)["status"] == "completed"
        assert worker.status()["pending_in_memory"] == 0
    finally:
        worker.stop()


def test_queue_start_rolls_back_when_pending_work_restore_fails(
    settings_factory, monkeypatch
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))
    worker.submit(job_id)
    original_entries = database.jobs.queued_entries
    failed = True

    def fail_once() -> list[dict[str, object]]:
        nonlocal failed
        if failed:
            failed = False
            raise RuntimeError("queue restore failed")
        return original_entries()

    monkeypatch.setattr(database.jobs, "queued_entries", fail_once)

    with pytest.raises(RuntimeError, match="queue restore failed"):
        worker.start()

    assert worker._context is None
    assert not worker.is_running
    assert worker.status()["pending_in_memory"] == 1

    worker.start()
    try:
        assert wait_terminal(database, job_id)["status"] == "completed"
    finally:
        worker.stop()


def test_queue_start_rolls_back_when_thread_start_fails(
    settings_factory, monkeypatch
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))
    worker.submit(job_id)
    original_start = threading.Thread.start
    failed = True

    def fail_once(thread: threading.Thread) -> None:
        nonlocal failed
        if failed and thread.name == "voice-gpu-worker":
            failed = False
            raise OSError("worker start failed")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_once)

    with pytest.raises(OSError, match="worker start failed"):
        worker.start()

    assert worker._context is None
    assert not worker.is_running
    assert worker.status()["pending_in_memory"] == 1

    worker.start()
    try:
        assert wait_terminal(database, job_id)["status"] == "completed"
    finally:
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


def test_queue_error_does_not_fail_reclaimed_work(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    item = database.jobs.items(job_id)[0]
    candidate = database.candidates.list_for_item(item["id"])[0]
    job_token = database.jobs.set_running(job_id)
    candidate_token = database.candidates.set_running(candidate["id"])
    assert job_token and candidate_token

    processor = JobProcessor(
        settings, database, FakeEngine(settings, SimpleNamespace()), threading.Event()
    )
    processor.fail_work("job", job_id, RuntimeError("stale job error"))
    processor.fail_work(
        "candidate", candidate["id"], RuntimeError("stale candidate error")
    )

    current_job = database.jobs.get(job_id)
    current_candidate = database.candidates.get(candidate["id"])
    assert current_job is not None and current_job["status"] == "running"
    assert current_job["run_token"] == job_token
    assert current_candidate is not None and current_candidate["status"] == "running"
    assert current_candidate["run_token"] == candidate_token

    processor.fail_work(
        "candidate",
        candidate["id"],
        RuntimeError("current candidate error"),
        run_token=candidate_token,
    )
    assert database.candidates.get(candidate["id"])["status"] == "failed"
    processor.fail_work(
        "job", job_id, RuntimeError("current job error"), run_token=job_token
    )
    assert database.jobs.get(job_id)["status"] == "failed"


def test_queue_error_logs_are_sanitized(settings_factory, caplog) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))

    class FailingProcessor:
        def fail_work(self, *args, **kwargs):
            raise RuntimeError(r"C:\private\api_key=secret")

    context = SimpleNamespace(processor=FailingProcessor())
    error = RuntimeError(r"C:\private\authorization=secret")
    with caplog.at_level(logging.ERROR, logger="app.queue_worker"):
        worker._handle_queue_error(context, QueueWork("job", "job-id"), error)

    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_queue_can_restart_and_recover_running_job(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = seed_job(database, settings.root)
    database.jobs.set_running(job_id)
    item = database.jobs.items(job_id)[0]
    database.jobs.set_item_running(item["id"])
    candidate = database.candidates.list_for_item(item["id"])[0]
    assert database.candidates.set_running(candidate["id"])

    assert database.jobs.recover_interrupted() == 1
    assert database.jobs.get(job_id)["status"] == "queued"
    assert database.jobs.items(job_id)[0]["status"] == "queued"
    recovered_candidate = database.candidates.get(candidate["id"])
    assert recovered_candidate is not None
    assert recovered_candidate["status"] == "queued"
    assert recovered_candidate["started_at"] is None
    assert database.jobs.recover_interrupted() == 0

    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))
    worker.start()
    worker.stop()
    worker.start()
    try:
        assert wait_terminal(database, job_id)["status"] == "completed"
    finally:
        worker.stop()


def test_queue_restart_uses_a_new_reference_path(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    first_job_id = seed_job(database, settings.root)

    class RecordingEngine(FakeEngine):
        def __init__(self, engine_settings, profiles) -> None:
            super().__init__(engine_settings, profiles)
            self.reference_paths: list[Path] = []

        def prepare_reference(self, voice_files, target_path):
            self.reference_paths.append(target_path)
            return super().prepare_reference(voice_files, target_path)

    engine = RecordingEngine(settings, SimpleNamespace())
    worker = JobQueue(settings, database, engine)
    worker.start()
    try:
        assert wait_terminal(database, first_job_id)["status"] == "completed"
        worker.stop()

        second_job_id = seed_job(database, settings.root)
        worker.start()
        assert wait_terminal(database, second_job_id)["status"] == "completed"
    finally:
        worker.stop()

    assert len(engine.reference_paths) == 2
    assert engine.reference_paths[0] != engine.reference_paths[1]


def test_start_during_timed_out_stop_restarts_worker(
    settings_factory, monkeypatch
) -> None:
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
            self.release.wait(timeout=2)
            return super().generate(*args, **kwargs)

    engine = BlockingEngine(settings, SimpleNamespace())
    worker = JobQueue(settings, database, engine)
    monkeypatch.setattr(queue_worker_module, "STOP_JOIN_TIMEOUT_SECONDS", 0.01)
    worker.start()
    try:
        assert engine.started.wait(timeout=2)
        worker.stop()
        assert worker.is_running
        worker.start()
        engine.release.set()
        assert wait_terminal(database, job_id)["status"] == "completed"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not worker.is_running:
            time.sleep(0.01)
        assert worker.is_running
    finally:
        engine.release.set()
        worker.stop()


def test_stop_releases_lifecycle_lock_before_joining(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    worker = JobQueue(settings, database, FakeEngine(settings, SimpleNamespace()))

    class LockCheckingThread:
        def __init__(self) -> None:
            self.alive = True
            self.joined = False

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            acquired = worker._lifecycle_lock.acquire(blocking=False)
            assert acquired
            worker._lifecycle_lock.release()
            self.joined = True
            self.alive = False

    thread = LockCheckingThread()
    worker._thread = thread

    worker.stop()

    assert thread.joined
    assert not worker.is_running
