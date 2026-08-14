from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .database import Database
from .engine_adapter import VoiceEngine
from .generation_settings import stored_generation_settings
from .settings import Settings


@dataclass(frozen=True)
class JobPaths:
    reference: Path
    audio: Path
    candidates: Path


class JobProcessor:
    """Execute queued GPT work and persist each state transition."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        engine: VoiceEngine,
        stop_event: threading.Event,
    ) -> None:
        self._settings = settings
        self._database = database
        self._engine = engine
        self._stop = stop_event

    def process_job(self, job_id: str) -> None:
        job = self._start_job(job_id)
        if not job:
            return
        items = self._database.jobs.items(job_id)
        completed = sum(_has_completed_output(item) for item in items)
        if completed == len(items):
            self._finish_completed_job(job_id, len(items))
            return
        paths = self._job_paths(job_id)
        reference = self._prepare_job_reference(job, paths.reference)
        if reference is not None:
            self._generate_pending_items(job, items, reference, paths, completed)

    def process_candidate(self, candidate_id: str) -> None:
        candidate = self._database.candidates.get(candidate_id)
        if not _is_queued_gpt_candidate(candidate):
            return
        job = self._database.jobs.get(str(candidate["job_id"]))
        if not job:
            self._database.candidates.finish(candidate_id, "failed", error="原生成任务已不存在")
            return
        paths = self._job_paths(str(job["id"]))
        try:
            reference = self._build_reference(job, paths.reference)
        except Exception as error:
            self._database.candidates.finish(
                candidate_id,
                "failed",
                error=f"参考音频准备失败: {error}",
            )
            return
        self._generate_candidate_audio(candidate, job, reference, paths)

    def fail_work(self, kind: str, item_id: str, error: Exception) -> None:
        message = f"队列内部错误: {error}"
        if kind == "candidate":
            self._database.candidates.finish(item_id, "failed", error=message)
            return
        self._database.jobs.fail_pending_items(item_id, message)
        self._database.jobs.finish(item_id, "failed", message)

    def _start_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._database.jobs.get(job_id)
        if not job or job["status"] != "queued":
            return None
        self._database.jobs.set_running(job_id)
        return self._database.jobs.get(job_id)

    def _job_paths(self, job_id: str) -> JobPaths:
        root = self._settings.job_root / job_id
        return JobPaths(
            reference=root / "reference.wav",
            audio=root / "audio",
            candidates=root / "candidates",
        )

    def _finish_completed_job(self, job_id: str, item_count: int) -> None:
        self._database.jobs.update_progress(job_id, item_count, 0)
        self._database.jobs.finish(job_id, "completed")

    def _prepare_job_reference(self, job: dict[str, Any], target: Path) -> Any | None:
        try:
            return self._build_reference(job, target)
        except Exception as error:
            message = f"参考音频准备失败: {error}"
            job_id = str(job["id"])
            self._database.jobs.fail_pending_items(job_id, message)
            self._database.jobs.finish(job_id, "failed", message)
            return None

    def _build_reference(self, job: dict[str, Any], target: Path) -> Any:
        return self._engine.prepare_reference(self._reference_files(job), target)

    def _reference_files(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        files = self._database.voices.list_files(str(job["voice_id"]))
        emotion = str(job.get("reference_emotion") or "all")
        if emotion == "all":
            return files
        return [item for item in files if item.get("emotion_tag") == emotion]

    def _generate_pending_items(
        self,
        job: dict[str, Any],
        items: list[dict[str, Any]],
        reference: Any,
        paths: JobPaths,
        completed: int,
    ) -> None:
        errors: list[str] = []
        elapsed_samples: list[float] = []
        job_id = str(job["id"])
        self._database.jobs.update_progress(job_id, completed, None)
        for item in items:
            if self._stop.is_set():
                self._database.jobs.requeue(job_id, "服务器重启后继续生成")
                return
            if _has_completed_output(item):
                continue
            elapsed, error = self._generate_item(job, item, reference, paths)
            if elapsed is not None:
                elapsed_samples.append(elapsed)
            if error:
                errors.append(f"第 {item['sequence']} 段: {error}")
            completed += 1
            self._update_progress(job_id, len(items), completed, elapsed_samples)
        self._finish_generation(job_id, errors)

    def _generate_item(
        self,
        job: dict[str, Any],
        item: dict[str, Any],
        reference: Any,
        paths: JobPaths,
    ) -> tuple[float | None, str | None]:
        item_id = str(item["id"])
        self._database.jobs.set_item_running(item_id)
        elapsed: list[float] = []
        errors: list[str] = []
        for candidate in self._initial_candidates(item_id):
            if _has_completed_output(candidate):
                continue
            seconds, error = self._generate_candidate_audio(
                candidate, job, reference, paths
            )
            if seconds is not None:
                elapsed.append(seconds)
            if error:
                errors.append(f"{candidate['name']}: {error}")
        completed = self._database.candidates.complete_job_item(item_id)
        if completed:
            return sum(elapsed) if elapsed else completed.get("elapsed_seconds"), None
        message = "；".join(errors) or "没有生成出可用候选"
        self._database.jobs.finish_item(item_id, "failed", error=message)
        return None, message

    def _initial_candidates(self, item_id: str) -> list[dict[str, Any]]:
        return [
            candidate
            for candidate in self._database.candidates.list_for_item(item_id)
            if candidate["origin_type"] in {"job_item", "generation"}
            and candidate["kind"] == "gpt"
        ]

    def _generate_candidate_audio(
        self,
        candidate: dict[str, Any],
        job: dict[str, Any],
        reference: Any,
        paths: JobPaths,
    ) -> tuple[float | None, str | None]:
        candidate_id = str(candidate["id"])
        self._database.candidates.set_running(candidate_id)
        try:
            result = self._engine.generate(
                item=candidate,
                reference=reference,
                model_id=str(job["model_id"]),
                seed=int(
                    candidate["seed"] if candidate.get("seed") is not None else 20260807
                ),
                output_path=_candidate_output_path(candidate, paths),
                generation_settings=_generation_settings(candidate),
            )
            self._finish_candidate(candidate_id, result)
            return float(result.elapsed_seconds), None
        except Exception as error:
            message = str(error)
            self._database.candidates.finish(candidate_id, "failed", error=message)
            return None, message

    def _finish_candidate(self, candidate_id: str, result: Any) -> None:
        self._database.candidates.finish(
            candidate_id,
            "completed",
            raw_audio_path=result.audio_path,
            audio_path=result.audio_path,
            duration_seconds=result.duration_seconds,
            elapsed_seconds=result.elapsed_seconds,
            processing_backend=result.processing_backend,
        )

    def _update_progress(
        self,
        job_id: str,
        total: int,
        completed: int,
        elapsed_samples: list[float],
    ) -> None:
        per_item = (
            median(elapsed_samples)
            if elapsed_samples
            else self._database.monitoring.estimated_item_seconds()
        )
        eta = round(max(0, total - completed) * per_item)
        self._database.jobs.update_progress(job_id, completed, eta)

    def _finish_generation(self, job_id: str, errors: list[str]) -> None:
        if errors:
            self._database.jobs.finish(job_id, "failed", "\n".join(errors))
        else:
            self._database.jobs.finish(job_id, "completed")


def _is_queued_gpt_candidate(candidate: dict[str, Any] | None) -> bool:
    return bool(
        candidate and candidate["status"] == "queued" and candidate["kind"] == "gpt"
    )


def _has_completed_output(item: dict[str, Any]) -> bool:
    return bool(
        item["status"] == "completed"
        and item["audio_path"]
        and Path(str(item["audio_path"])).is_file()
    )


def _generation_settings(candidate: dict[str, Any]) -> dict[str, float | int]:
    return stored_generation_settings(candidate.get("generation_settings_json"))


def _candidate_output_path(candidate: dict[str, Any], paths: JobPaths) -> Path:
    sequence = int(candidate["sequence"])
    if candidate["origin_type"] == "job_item":
        return paths.audio / f"{sequence:03d}.wav"
    root = paths.candidates / f"{sequence:03d}" / str(candidate["id"])
    return root / "audio.wav"
