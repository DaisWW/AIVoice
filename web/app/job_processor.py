from __future__ import annotations

import json
import math
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .database import Database
from .domain import MAX_SCRIPT_ITEMS
from .engine_adapter import VoiceEngine
from .error_utils import safe_error_message
from .generation_settings import stored_generation_settings
from .settings import Settings
from .storage import ensure_within, resolve_audio_path


_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_SEED = 2_147_483_647


@dataclass(frozen=True)
class JobPaths:
    reference: Path
    audio: Path
    candidates: Path


class QueuePersistenceError(RuntimeError):
    """Carry a claimed work lease through a failed error-state write."""

    def __init__(
        self,
        kind: str,
        item_id: str,
        run_token: str | None,
        cause: Exception,
    ) -> None:
        self.kind = kind
        self.item_id = item_id
        self.run_token = run_token
        super().__init__(safe_error_message(cause, "队列处理失败"))


class JobProcessor:
    """Execute queued clone work and persist each state transition."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        engine: VoiceEngine,
        stop_event: threading.Event,
        generation: int | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._engine = engine
        self._stop = stop_event
        self._generation = generation

    def process_job(self, job_id: str) -> None:
        job_token: str | None = None
        try:
            claimed = self._start_job(job_id)
            if not claimed:
                return
            job, job_token = claimed
            items = self._database.jobs.items(job_id)
            completed = sum(
                _has_completed_output(item, self._settings.root) for item in items
            )
            if completed == len(items):
                self._finish_completed_job(job_id, len(items), job_token)
                return
            paths = self._job_paths(job_id)
            reference = self._prepare_job_reference(job, paths.reference, job_token)
            if reference is not None:
                self._generate_pending_items(
                    job, items, reference, paths, completed, job_token
                )
        except Exception as error:
            message = "队列内部错误: " + safe_error_message(error, "队列处理失败")
            try:
                if job_token:
                    self._database.jobs.fail_if_token(job_id, job_token, message)
                else:
                    self._database.jobs.fail_queued(job_id, message)
            except Exception as persist_error:
                raise QueuePersistenceError(
                    "job", job_id, job_token, error
                ) from persist_error

    def process_candidate(self, candidate_id: str) -> None:
        candidate: dict[str, Any] | None = None
        candidate_token: str | None = None
        try:
            candidate = self._database.candidates.get(candidate_id)
            if not _is_queued_gpt_candidate(candidate):
                return
            candidate_token = self._database.candidates.set_running(candidate_id)
            if not candidate_token:
                return
            try:
                job = self._database.jobs.get(str(candidate["job_id"]))
                if not job:
                    self._database.candidates.fail_if_token(
                        candidate_id, candidate_token, "原生成任务已不存在"
                    )
                    return
                item_id = str(candidate["job_item_id"])
                item = self._database.jobs.item(str(job["id"]), item_id)
                if not item:
                    self._database.candidates.fail_if_token(
                        candidate_id, candidate_token, "原台词段落已不存在"
                    )
                    return
                expected_item_status = str(item.get("status") or "")
                paths = self._job_paths(str(job["id"]))
                reference = self._build_reference(job, paths.reference)
            except Exception as error:
                self._database.candidates.fail_if_token(
                    candidate_id,
                    candidate_token,
                    "参考音频准备失败: " + safe_error_message(error, "无法准备参考音频"),
                )
                return
            seconds, _, lease_lost = self._generate_candidate_audio(
                candidate,
                job,
                reference,
                paths,
                candidate_token=candidate_token,
                already_running=True,
            )
            if seconds is not None and not lease_lost:
                self._database.candidates.complete_job_item(
                    str(candidate["job_item_id"]),
                    self._settings.root,
                    expected_item_status=expected_item_status,
                )
                self._database.jobs.refresh_summary(str(job["id"]))
        except Exception as error:
            message = "队列内部错误: " + safe_error_message(error, "队列处理失败")
            try:
                if candidate_token:
                    self._database.candidates.fail_if_token(
                        candidate_id, candidate_token, message
                    )
                else:
                    self._database.candidates.fail_queued(candidate_id, message)
            except Exception as persist_error:
                raise QueuePersistenceError(
                    "candidate", candidate_id, candidate_token, error
                ) from persist_error

    def fail_work(
        self,
        kind: str,
        item_id: str,
        error: Exception,
        run_token: str | None = None,
    ) -> None:
        message = f"队列内部错误: {safe_error_message(error, '队列处理失败')}"
        if kind == "candidate":
            if run_token:
                self._database.candidates.fail_if_token(item_id, run_token, message)
            else:
                self._database.candidates.fail_queued(item_id, message)
            return
        if run_token:
            self._database.jobs.fail_if_token(item_id, run_token, message)
        else:
            self._database.jobs.fail_queued(item_id, message)

    def _start_job(self, job_id: str) -> tuple[dict[str, Any], str] | None:
        job = self._database.jobs.get(job_id)
        if not job or job["status"] != "queued":
            return None
        token = self._database.jobs.set_running(job_id)
        if not token:
            return None
        return {**job, "status": "running", "run_token": token}, token

    def _job_paths(self, job_id: str) -> JobPaths:
        component = _safe_path_component(job_id, "任务编号无效")
        root = ensure_within(
            self._settings.job_root / component,
            self._settings.job_root,
        )
        reference_name = (
            "reference.wav"
            if self._generation is None
            else f"reference.{self._generation}.wav"
        )
        return JobPaths(
            reference=root / reference_name,
            audio=root / "audio",
            candidates=root / "candidates",
        )

    def _finish_completed_job(self, job_id: str, item_count: int, token: str) -> None:
        if self._database.jobs.update_progress_if_token(job_id, token, item_count, 0):
            self._database.jobs.finish_if_token(job_id, token, "completed")

    def _prepare_job_reference(
        self, job: dict[str, Any], target: Path, job_token: str
    ) -> Any | None:
        try:
            return self._build_reference(job, target)
        except Exception as error:
            message = "参考音频准备失败: " + safe_error_message(error, "无法准备参考音频")
            job_id = str(job["id"])
            self._database.jobs.fail_if_token(job_id, job_token, message)
            return None

    def _build_reference(self, job: dict[str, Any], target: Path) -> Any:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
        if target.exists():
            target.replace(backup)
        try:
            reference = self._engine.prepare_reference(
                self._reference_files(job), target
            )
        except Exception:
            target.unlink(missing_ok=True)
            if backup.exists():
                backup.replace(target)
            raise
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            # The generated reference is already durable; a stale backup is
            # harmless and must not turn a successful job into a failure.
            pass
        return reference

    def _reference_files(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = job.get("reference_files_json")
        snapshot_version = _strict_int(
            job.get("reference_snapshot_version"),
            "任务参考音快照版本无效",
            default=0,
            minimum=0,
            maximum=1,
        )
        if snapshot_version >= 1 or str(snapshot or "").strip() not in {"", "[]"}:
            try:
                value = json.loads(str(snapshot))
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError("任务参考音快照损坏") from error
            if not isinstance(value, list):
                raise ValueError("任务参考音快照格式无效")
            if not value and snapshot_version >= 1:
                raise ValueError("任务参考音快照为空")
            if value:
                return self._validated_snapshot_files(job, value)
        files = self._database.voices.list_files(str(job["voice_id"]))
        emotion = str(job.get("reference_emotion") or "all")
        if emotion == "all":
            selected = files
        else:
            selected = [item for item in files if item.get("emotion_tag") == emotion]
        return self._validated_snapshot_files(job, selected)

    def _validated_snapshot_files(
        self, job: dict[str, Any], value: list[Any]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict) or not item.get("source_path"):
                raise ValueError("任务参考音快照格式无效")
            try:
                path = ensure_within(
                    Path(str(item["source_path"])), self._settings.root
                )
            except ValueError as error:
                raise ValueError("任务参考音快照路径无效") from error
            if not path.is_file():
                raise ValueError(f"参考录音快照已不存在: {path.name}")
            result.append(
                {
                    **item,
                    "source_path": str(path),
                    "voice_name": str(job.get("voice_name") or ""),
                }
            )
        return result

    def _generate_pending_items(
        self,
        job: dict[str, Any],
        items: list[dict[str, Any]],
        reference: Any,
        paths: JobPaths,
        completed: int,
        job_token: str,
    ) -> None:
        errors: list[str] = []
        elapsed_samples: list[float] = []
        job_id = str(job["id"])
        if not self._database.jobs.update_progress_if_token(
            job_id, job_token, completed, None
        ):
            return
        for item in items:
            if self._stop.is_set():
                self._database.jobs.requeue_if_token(job_id, job_token, "服务器重启后继续生成")
                return
            if _has_completed_output(item, self._settings.root):
                continue
            elapsed, error, lease_lost = self._generate_item(
                job, item, reference, paths, job_token
            )
            if lease_lost:
                return
            if elapsed is not None:
                elapsed_samples.append(elapsed)
            if error:
                errors.append(f"第 {item['sequence']} 段: {error}")
            current = self._database.jobs.item(job_id, str(item["id"]))
            if current and _has_completed_output(current, self._settings.root):
                completed += 1
            if not self._update_progress(
                job_id, job_token, len(items), completed, elapsed_samples
            ):
                return
        self._finish_generation(job_id, job_token, errors)

    def _generate_item(
        self,
        job: dict[str, Any],
        item: dict[str, Any],
        reference: Any,
        paths: JobPaths,
        job_token: str,
    ) -> tuple[float | None, str | None, bool]:
        item_id = str(item["id"])
        if item.get("status") == "completed" and not _has_completed_output(
            item, self._settings.root
        ):
            self._database.jobs.requeue_missing_item(item_id)
        item_token = self._database.jobs.set_item_running(item_id)
        if not item_token:
            return None, "台词状态已变化，已跳过本次生成", False
        elapsed: list[float] = []
        errors: list[str] = []
        for candidate in self._initial_candidates(item_id):
            if _has_completed_output(candidate, self._settings.root):
                continue
            if candidate.get("status") == "completed":
                if not self._database.candidates.requeue_missing_output(
                    str(candidate["id"])
                ):
                    continue
                candidate = {**candidate, "status": "queued", "audio_path": ""}
            seconds, error, lease_lost = self._generate_candidate_audio(
                candidate,
                job,
                reference,
                paths,
                candidate_token=None,
            )
            if lease_lost:
                return None, None, True
            if seconds is not None:
                elapsed.append(seconds)
            if error:
                errors.append(f"{candidate['name']}: {error}")
        completed = self._database.candidates.complete_job_item(
            item_id, self._settings.root, item_token
        )
        if completed:
            return (
                sum(elapsed) if elapsed else completed.get("elapsed_seconds"),
                None,
                False,
            )
        message = "；".join(errors) or "没有生成出可用候选"
        if not self._database.jobs.finish_item_if_token(
            item_id, item_token, "failed", error=message
        ):
            return None, None, True
        return None, message, False

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
        *,
        candidate_token: str | None = None,
        already_running: bool = False,
    ) -> tuple[float | None, str | None, bool]:
        candidate_id = str(candidate["id"])
        if not already_running:
            candidate_token = self._database.candidates.set_running(candidate_id)
            if not candidate_token:
                return None, None, False
        if not candidate_token:
            return None, None, True
        temporary_path: Path | None = None
        cleanup_root = (
            paths.audio if candidate["origin_type"] == "job_item" else paths.candidates
        )
        try:
            final_path = _candidate_output_path(candidate, paths)
            temporary_path = _lease_output_path(final_path, candidate_token)
            result = self._engine.generate(
                item=candidate,
                reference=reference,
                model_id=str(job["model_id"]),
                seed=_candidate_seed(candidate),
                output_path=temporary_path,
                generation_settings=_generation_settings(candidate),
            )
            generated_path = ensure_within(
                Path(str(result.audio_path)), self._settings.root
            )
            if generated_path != temporary_path or not temporary_path.is_file():
                raise ValueError("模型输出文件无效")
            duration = _finite_nonnegative(result.duration_seconds, "音频时长无效")
            elapsed = _finite_nonnegative(result.elapsed_seconds, "生成耗时无效")
            completed = self._database.candidates.complete_if_token(
                candidate_id,
                candidate_token,
                temporary_path,
                final_path,
                self._settings.root,
                duration,
                elapsed,
                str(result.processing_backend or ""),
            )
            if not completed:
                _remove_file(temporary_path, cleanup_root)
                return None, None, True
            return elapsed, None, False
        except Exception as error:
            message = safe_error_message(error, "音频生成失败")
            if temporary_path is not None:
                _remove_file(temporary_path, cleanup_root)
            if self._database.candidates.fail_if_token(
                candidate_id, candidate_token, message
            ):
                return None, message, False
            return None, None, True

    def _update_progress(
        self,
        job_id: str,
        job_token: str,
        total: int,
        completed: int,
        elapsed_samples: list[float],
    ) -> bool:
        per_item = (
            median(elapsed_samples)
            if elapsed_samples
            else self._database.monitoring.estimated_item_seconds()
        )
        eta = round(max(0, total - completed) * per_item)
        return self._database.jobs.update_progress_if_token(
            job_id, job_token, completed, eta
        )

    def _finish_generation(
        self, job_id: str, job_token: str, errors: list[str]
    ) -> None:
        if errors:
            self._database.jobs.finish_if_token(
                job_id, job_token, "failed", "\n".join(errors)
            )
        else:
            self._database.jobs.finish_if_token(job_id, job_token, "completed")


def _is_queued_gpt_candidate(candidate: dict[str, Any] | None) -> bool:
    return bool(
        candidate and candidate["status"] == "queued" and candidate["kind"] == "gpt"
    )


def _has_completed_output(item: dict[str, Any], root: Path | None = None) -> bool:
    if item.get("status") != "completed":
        return False
    if root is not None:
        return resolve_audio_path(item, root) is not None
    for field in ("raw_audio_path", "audio_path"):
        value = str(item.get(field) or "").strip()
        if value and Path(value).is_file():
            return True
    return False


def _generation_settings(candidate: dict[str, Any]) -> dict[str, float | int]:
    return stored_generation_settings(candidate.get("generation_settings_json"))


def _lease_output_path(final_path: Path, token: str) -> Path:
    return final_path.with_name(f".{final_path.stem}.{token}{final_path.suffix}")


def _finite_nonnegative(value: Any, message: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(message) from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(message)
    return parsed


def _candidate_output_path(candidate: dict[str, Any], paths: JobPaths) -> Path:
    sequence = _strict_int(
        candidate.get("sequence"),
        "候选序号无效",
        minimum=1,
        maximum=MAX_SCRIPT_ITEMS,
    )
    if candidate["origin_type"] == "job_item":
        return ensure_within(paths.audio / f"{sequence:03d}.wav", paths.audio)
    candidate_id = _safe_path_component(candidate.get("id"), "候选编号无效")
    root = ensure_within(
        paths.candidates / f"{sequence:03d}" / candidate_id, paths.candidates
    )
    return ensure_within(root / "audio.wav", paths.candidates)


def _candidate_seed(candidate: dict[str, Any]) -> int:
    value = candidate.get("seed")
    if value is None:
        return 20260807
    return _strict_int(value, "候选随机种子无效", minimum=0, maximum=MAX_SEED)


def _strict_int(
    value: Any,
    message: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None and default is not None:
        parsed = default
    else:
        if isinstance(value, bool) or (
            isinstance(value, float) and not value.is_integer()
        ):
            raise ValueError(message)
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(message) from error
    if minimum is not None and parsed < minimum:
        raise ValueError(message)
    if maximum is not None and parsed > maximum:
        raise ValueError(message)
    return parsed


def _safe_path_component(value: Any, message: str) -> str:
    component = str(value or "")
    if not _PATH_COMPONENT_RE.fullmatch(component):
        raise ValueError(message)
    return component


def _remove_file(path: Path, stop: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    _remove_empty_parents(path.parent, stop)


def _remove_empty_parents(directory: Path, stop: Path) -> None:
    current = directory
    while current != stop and current.is_dir():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
