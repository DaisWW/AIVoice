from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .database import Database
from .error_utils import safe_exception_summary
from .text_generation import TextGenerationService


ProgressCallback = Callable[..., None]
RunOperation = Callable[[ProgressCallback], Any]
MAX_PENDING_RUNS = 32


class TextGenerationRunner:
    """Run remote text requests off the FastAPI event loop with durable state."""

    def __init__(self, database: Database, service: TextGenerationService) -> None:
        self._database = database
        self._service = service
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="voice-text-worker"
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future[Any]] = {}
        self._slots = threading.BoundedSemaphore(MAX_PENDING_RUNS)
        self._stopped = False

    def submit(self, run_id: str, operation: RunOperation) -> None:
        if not self._slots.acquire(blocking=False):
            raise RuntimeError("文本生成队列已满，请稍后再试")
        with self._lock:
            if self._stopped:
                self._slots.release()
                raise RuntimeError("文本生成服务正在停止")
            try:
                future = self._executor.submit(self._execute, run_id, operation)
            except Exception:
                self._slots.release()
                raise
            self._futures[run_id] = future
            future.add_done_callback(lambda _: self._forget(run_id))

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self._database.text_generation_runs.fail_unfinished()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _forget(self, run_id: str) -> None:
        with self._lock:
            self._futures.pop(run_id, None)
        self._slots.release()

    def _execute(self, run_id: str, operation: RunOperation) -> None:
        runs = self._database.text_generation_runs
        if not runs.mark_running(run_id):
            return
        last_output = ""

        def progress(
            *, stage: str | None = None, output_text: str | None = None
        ) -> None:
            nonlocal last_output
            if output_text is not None:
                last_output = str(output_text)
            runs.update_progress(run_id, stage=stage, output_text=output_text)

        try:
            result = operation(progress)
            output_text = _output_text(result)
            runs.complete(run_id, output_text=output_text, result=result)
        except Exception as error:  # noqa: BLE001 - persist a safe user-facing failure
            runs.fail(
                run_id,
                error=safe_exception_summary(error, "文本生成失败"),
                output_text=last_output,
            )


def _output_text(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("suggestion", "output", "text"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        batch = result.get("batch")
        if isinstance(batch, dict):
            return (
                f"已生成 {len(batch.get('drafts') or [])} 个台本候选，"
                f"{int(batch.get('dialogue_line_count') or 0)} 条台词"
            )
        lines = result.get("lines")
        if isinstance(lines, list):
            values = [
                str(item.get("text") or item.get("pronunciation") or "")
                for item in lines
                if isinstance(item, dict)
            ]
            return "\n".join(value for value in values if value)
        line = result.get("line")
        if isinstance(line, dict):
            return str(line.get("text") or "")
    return str(result or "")
