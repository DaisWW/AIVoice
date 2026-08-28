from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from .database import Database
from .engine_adapter import VoiceEngine
from .error_utils import safe_exception_summary
from .job_processor import JobProcessor
from .settings import Settings


logger = logging.getLogger(__name__)
STOP_JOIN_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class QueueWork:
    kind: str
    item_id: str


@dataclass
class _WorkerContext:
    generation: int
    stop: threading.Event
    queue: queue.Queue[QueueWork | None]
    processor: JobProcessor
    pending_ids: set[tuple[str, str]]
    stop_signal_sent: bool = False
    thread: threading.Thread | None = None


class JobQueue:
    """Serialize all GPU work onto one restartable worker thread."""

    def __init__(
        self, settings: Settings, database: Database, engine: VoiceEngine
    ) -> None:
        self._settings = settings
        self._database = database
        self._engine = engine
        self._idle_queue: queue.Queue[QueueWork | None] = queue.Queue()
        self._idle_stop = threading.Event()
        self._idle_pending_ids: set[tuple[str, str]] = set()
        self._idle_stop_signal_sent = False
        self._idle_processor = JobProcessor(settings, database, engine, self._idle_stop)
        self._context: _WorkerContext | None = None
        self._generation = 0
        # Keep these aliases for diagnostics and existing integrations that
        # inspect queue state while preserving all worker-owned state in the
        # context above.
        self._queue: queue.Queue[QueueWork | None] = self._idle_queue
        self._stop = self._idle_stop
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._pending_ids: set[tuple[str, str]] = self._idle_pending_ids
        self._thread: threading.Thread | None = None
        self._desired_running = False
        self._processor = self._idle_processor

    @property
    def is_running(self) -> bool:
        with self._lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                thread = self._thread
            if thread is not None:
                if thread.is_alive():
                    self._desired_running = True
                    return
                thread.join()
                with self._lock:
                    if self._thread is thread:
                        self._thread = None
                        self._set_context_locked(None)
            self._desired_running = True
            self._launch_locked()

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                self._desired_running = False
                thread = self._thread
                context = self._context
                running = thread is not None and thread.is_alive()
                if running:
                    stop_event = context.stop if context is not None else self._stop
                    worker_queue = context.queue if context is not None else self._queue
                    stop_event.set()
                    if context is not None:
                        if not context.stop_signal_sent:
                            worker_queue.put(None)
                            context.stop_signal_sent = True
                    elif not self._idle_stop_signal_sent:
                        worker_queue.put(None)
                        self._idle_stop_signal_sent = True
        if running and thread is not None:
            thread.join(timeout=STOP_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning("GPU worker is still finishing the current inference")
                return
        with self._lifecycle_lock:
            with self._lock:
                if self._thread is thread:
                    self._thread = None
                    self._set_context_locked(None)

    def _launch_locked(self) -> None:
        """Start a worker; the lifecycle lock must already be held."""
        with self._lock:
            idle_work = self._drain_idle_locked()
        self._generation += 1
        stop_event = threading.Event()
        context = _WorkerContext(
            generation=self._generation,
            stop=stop_event,
            queue=queue.Queue(),
            processor=JobProcessor(
                self._settings,
                self._database,
                self._engine,
                stop_event,
                generation=self._generation,
            ),
            pending_ids=set(),
        )
        with self._lock:
            self._set_context_locked(context)
            for work in idle_work:
                self._enqueue_locked(context, work)
        try:
            self._restore_pending_work(context)
        except Exception:
            self._rollback_launch(context)
            raise
        with self._lock:
            if self._context is not context:
                return
        thread = threading.Thread(
            target=self._run, args=(context,), name="voice-gpu-worker", daemon=True
        )
        context.thread = thread
        with self._lock:
            if self._context is context:
                self._thread = thread
        try:
            thread.start()
        except Exception:
            self._rollback_launch(context)
            raise

    def _rollback_launch(self, context: _WorkerContext) -> None:
        """Return queued work to the idle buffer when worker startup fails."""
        with self._lock:
            if self._context is not context:
                return
            pending: list[QueueWork] = []
            while True:
                try:
                    work = context.queue.get_nowait()
                except queue.Empty:
                    break
                context.queue.task_done()
                if work is not None:
                    pending.append(work)
            if self._thread is context.thread:
                self._thread = None
            self._set_context_locked(None)
            for work in pending:
                self._enqueue_locked(None, work)

    def _drain_idle_locked(self) -> list[QueueWork]:
        work_items: list[QueueWork] = []
        while True:
            try:
                work = self._idle_queue.get_nowait()
            except queue.Empty:
                break
            self._idle_queue.task_done()
            if work is not None:
                work_items.append(work)
        self._idle_pending_ids.clear()
        self._idle_stop_signal_sent = False
        return work_items

    def submit(self, job_id: str) -> None:
        self._submit(QueueWork("job", job_id))

    def submit_candidate(self, candidate_id: str) -> None:
        self._submit(QueueWork("candidate", candidate_id))

    def _restore_pending_work(self, context: _WorkerContext) -> None:
        jobs = self._database.jobs.queued_entries()
        candidates = self._database.candidates.queued_manual_entries("gpt")
        pending = [
            (str(entry["submitted_at"]), "job", str(entry["id"])) for entry in jobs
        ] + [
            (str(entry["submitted_at"]), "candidate", str(entry["id"]))
            for entry in candidates
        ]
        with self._lock:
            if self._context is not context:
                return
            for _, kind, item_id in sorted(
                pending, key=lambda item: (item[0], item[2])
            ):
                self._enqueue_locked(context, QueueWork(kind, item_id))

    def _submit(self, work: QueueWork) -> None:
        with self._lock:
            context = self._context
            self._enqueue_locked(context, work)

    def _enqueue_locked(self, context: _WorkerContext | None, work: QueueWork) -> None:
        if context is None:
            pending_ids = self._idle_pending_ids
            worker_queue = self._idle_queue
        else:
            pending_ids = context.pending_ids
            worker_queue = context.queue
        key = (work.kind, work.item_id)
        if key in pending_ids:
            return
        pending_ids.add(key)
        worker_queue.put(work)

    def _run(self, context: _WorkerContext) -> None:
        try:
            while not context.stop.is_set():
                try:
                    work = context.queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if work is None:
                    context.queue.task_done()
                    return
                try:
                    self._dispatch(context, work)
                except Exception as error:
                    self._handle_queue_error(context, work, error)
                finally:
                    self._complete(context, work)
                    context.queue.task_done()
        finally:
            self._finish_context(context)

    def _finish_context(self, context: _WorkerContext) -> None:
        with self._lock:
            current = (
                self._context is context and self._thread is threading.current_thread()
            )
        if current:
            unload = getattr(self._engine, "unload", None)
            if callable(unload):
                try:
                    unload()
                except Exception as error:
                    logger.error(
                        "Failed to unload voice engine during shutdown (%s)",
                        safe_exception_summary(error, "引擎释放失败"),
                    )
            with self._lock:
                if (
                    self._context is context
                    and self._thread is threading.current_thread()
                ):
                    self._thread = None
                    self._set_context_locked(None)
                    restart = self._desired_running
                else:
                    restart = False
        else:
            restart = False
        if restart:
            with self._lifecycle_lock:
                with self._lock:
                    restart = self._desired_running and self._context is None
                if restart:
                    try:
                        self._launch_locked()
                    except Exception as error:
                        self._desired_running = False
                        logger.error(
                            "Failed to restart GPU worker (%s)",
                            safe_exception_summary(error, "队列重启失败"),
                        )

    def _set_context_locked(self, context: _WorkerContext | None) -> None:
        if context is None:
            self._context = None
            self._queue = self._idle_queue
            self._stop = self._idle_stop
            self._pending_ids = self._idle_pending_ids
            self._processor = self._idle_processor
            self._idle_stop_signal_sent = False
            return
        self._context = context
        self._queue = context.queue
        self._stop = context.stop
        self._pending_ids = context.pending_ids
        self._processor = context.processor

    def _dispatch(self, context: _WorkerContext, work: QueueWork) -> None:
        if work.kind == "candidate":
            context.processor.process_candidate(work.item_id)
        else:
            context.processor.process_job(work.item_id)

    def _complete(self, context: _WorkerContext, work: QueueWork) -> None:
        with self._lock:
            context.pending_ids.discard((work.kind, work.item_id))

    def _handle_queue_error(
        self, context: _WorkerContext, work: QueueWork, error: Exception
    ) -> None:
        logger.error(
            "Unhandled queue error for %s %s (%s)",
            work.kind,
            work.item_id,
            safe_exception_summary(error, "队列处理失败"),
        )
        try:
            run_token = getattr(error, "run_token", None)
            context.processor.fail_work(
                work.kind, work.item_id, error, run_token=run_token
            )
        except Exception as persist_error:
            logger.error(
                "Failed to persist queue error for %s %s (%s)",
                work.kind,
                work.item_id,
                safe_exception_summary(persist_error, "队列错误持久化失败"),
            )

    def status(self) -> dict[str, int | bool]:
        with self._lock:
            worker_queue = self._queue
        return {
            "worker_alive": self.is_running,
            "pending_in_memory": worker_queue.qsize(),
        }
