from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from .database import Database
from .engine_adapter import VoiceEngine
from .job_processor import JobProcessor
from .settings import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueueWork:
    kind: str
    item_id: str


class JobQueue:
    """Serialize all GPU work onto one restartable worker thread."""

    def __init__(
        self, settings: Settings, database: Database, engine: VoiceEngine
    ) -> None:
        self._database = database
        self._queue: queue.Queue[QueueWork | None] = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._pending_ids: set[tuple[str, str]] = set()
        self._thread: threading.Thread | None = None
        self._processor = JobProcessor(settings, database, engine, self._stop)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._queue = queue.Queue()
        with self._lock:
            self._pending_ids.clear()
        self._thread = threading.Thread(
            target=self._run, name="voice-gpu-worker", daemon=True
        )
        self._thread.start()
        self._restore_pending_work()

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop.set()
        self._queue.put(None)
        if self._thread is None:  # pragma: no cover - guarded by is_running
            return
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            logger.warning("GPU worker is still finishing the current inference")

    def submit(self, job_id: str) -> None:
        self._submit(QueueWork("job", job_id))

    def submit_candidate(self, candidate_id: str) -> None:
        self._submit(QueueWork("candidate", candidate_id))

    def _restore_pending_work(self) -> None:
        for job_id in self._database.jobs.queued_ids():
            self.submit(job_id)
        for candidate_id in self._database.candidates.queued_manual_ids("gpt"):
            self.submit_candidate(candidate_id)

    def _submit(self, work: QueueWork) -> None:
        key = (work.kind, work.item_id)
        with self._lock:
            if key in self._pending_ids:
                return
            self._pending_ids.add(key)
            self._queue.put(work)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                work = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if work is None:
                self._queue.task_done()
                return
            try:
                self._dispatch(work)
            except Exception as error:
                self._handle_queue_error(work, error)
            finally:
                self._complete(work)
                self._queue.task_done()

    def _dispatch(self, work: QueueWork) -> None:
        if work.kind == "candidate":
            self._processor.process_candidate(work.item_id)
        else:
            self._processor.process_job(work.item_id)

    def _complete(self, work: QueueWork) -> None:
        with self._lock:
            self._pending_ids.discard((work.kind, work.item_id))

    def _handle_queue_error(self, work: QueueWork, error: Exception) -> None:
        logger.exception("Unhandled queue error for %s", work.item_id)
        try:
            self._processor.fail_work(work.kind, work.item_id, error)
        except Exception:
            logger.exception("Failed to persist queue error for %s", work.item_id)

    def status(self) -> dict[str, int | bool]:
        return {
            "worker_alive": self.is_running,
            "pending_in_memory": self._queue.qsize(),
        }
