from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_ROOT))

from app.domain import ScriptItem  # noqa: E402
from app.engines.registry import VoiceEngine  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.profiles import Profiles  # noqa: E402
from app.provider_config import ProviderConfigStore  # noqa: E402
from app.queue_worker import JobQueue  # noqa: E402


DEFAULT_MODELS = (
    "gpt_sovits_v2_stable",
    "gpt_sovits_v2_pro_plus_stable",
    "gpt_sovits_v2_pro_plus_expressive",
    "qwen3_tts_0_6b_base",
    "qwen3_tts_1_7b_base",
)

DEFAULT_CASES = (
    {
        "id": "normal_short",
        "text": "陌生人，可否听我讲一段故事？",
    },
    {
        "id": "normal_long",
        "text": "我知道这条路很长，但是只要你愿意继续走下去，我们终究会找到答案。",
    },
    {
        "id": "polyphone",
        "text": "银行行长终于行走在长满青苔的桥上。",
    },
    {
        "id": "mixed_language",
        "text": "Wait，别急！这个 plan 还没有结束。",
    },
    {
        "id": "emotion",
        "text": "你说什么？再说一遍！",
    },
    {
        "id": "breath_pause",
        "text": "嗯……好吧，我再试一次。",
    },
)


@dataclass(frozen=True)
class BenchmarkSettings:
    """Use the real model root with an isolated runtime/data root."""

    root: Path
    runtime_root: Path

    @property
    def data_root(self) -> Path:
        return self.runtime_root / "data"

    @property
    def job_root(self) -> Path:
        return self.runtime_root / "jobs"

    @property
    def export_root(self) -> Path:
        return self.runtime_root / "exports"

    @property
    def database_path(self) -> Path:
        return self.runtime_root / "benchmark.sqlite3"

    @property
    def provider_config_path(self) -> Path:
        return self.runtime_root / "provider-settings.json"

    @property
    def clone_config_path(self) -> Path:
        return self.root / "code" / "gpt_sovits_config.json"

    @property
    def profiles_path(self) -> Path:
        return self.root / "web" / "config" / "profiles.json"

    def ensure_runtime(self) -> None:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)


class GpuSampler:
    def __init__(self, interval_seconds: float = 0.5) -> None:
        self._interval = max(0.1, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0
        self.samples: list[dict[str, Any]] = []

    def start(self) -> None:
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="clone-gpu-sampler")
        self._thread.daemon = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = query_gpu()
            if sample:
                sample["elapsed_seconds"] = round(
                    time.monotonic() - self._started_at, 3
                )
                self.samples.append(sample)
            self._stop.wait(self._interval)


def query_gpu() -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = next(
        (item.strip() for item in completed.stdout.splitlines() if item.strip()), ""
    )
    fields = [item.strip() for item in line.split(",")]
    if len(fields) < 6:
        return None
    try:
        return {
            "name": fields[0],
            "memory_total_mib": int(fields[1]),
            "memory_used_mib": int(fields[2]),
            "memory_free_mib": int(fields[3]),
            "utilization_percent": int(fields[4]),
            "temperature_c": int(fields[5]),
        }
    except (TypeError, ValueError):
        return None


def torch_memory() -> dict[str, Any]:
    try:
        import torch

        if not torch.cuda.is_available():
            return {"cuda_available": False}
        torch.cuda.synchronize()
        return {
            "cuda_available": True,
            "device": torch.cuda.get_device_name(0),
            "allocated_mib": round(torch.cuda.memory_allocated() / 2**20, 1),
            "reserved_mib": round(torch.cuda.memory_reserved() / 2**20, 1),
            "max_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
            "max_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
        }
    except Exception as error:  # pragma: no cover - diagnostic fallback
        return {"cuda_available": False, "error": type(error).__name__}


def reset_torch_peaks() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        return


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    resolved = (PROJECT_ROOT / path if not path.is_absolute() else path).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise ValueError(f"路径必须位于项目目录内: {resolved}") from error
    return resolved


def default_references() -> list[Path]:
    suqi_root = PROJECT_ROOT / "input" / "voices" / "audio" / "suqi"
    suqi_pair = [suqi_root / "suqi_5.WAV", suqi_root / "suqi_1.WAV"]
    if all(path.is_file() for path in suqi_pair):
        return suqi_pair
    validation = PROJECT_ROOT / "output" / "model-validation" / "reference.wav"
    if validation.is_file():
        return [validation]
    return [suqi_pair[0]]


def safe_slug(value: str) -> str:
    result = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value
    )
    return result.strip("._")[:96] or "run"


def load_cases(path: Path | None, include_raw: bool) -> list[dict[str, Any]]:
    if path is None:
        cases = [dict(item) for item in DEFAULT_CASES]
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"测试用例文件无法读取: {path}") from error
        if isinstance(payload, dict):
            payload = payload.get("cases")
        if not isinstance(payload, list):
            raise ValueError("测试用例文件必须是数组，或包含 cases 数组")
        cases = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index} 个测试用例不是对象")
            cases.append(dict(item))
    if include_raw:
        cases.append(
            {
                "id": "raw_phoneme",
                "text": "mo-la-na",
                "pronunciation": "raw: mo-la-na",
                "generated_text": "mo-la-na",
                "raw_mode": True,
            }
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(cases, start=1):
        case_id = str(item.get("id") or f"case_{index}").strip()
        text = str(item.get("text") or item.get("generated_text") or "").strip()
        if not text:
            raise ValueError(f"测试用例 {case_id} 没有 text")
        if case_id in seen:
            raise ValueError(f"测试用例 id 重复: {case_id}")
        seen.add(case_id)
        result.append(
            {
                "id": case_id,
                "text": text,
                "pronunciation": str(item.get("pronunciation") or ""),
                "generated_text": str(item.get("generated_text") or text),
                "raw_mode": bool(item.get("raw_mode", False)),
                "direction": str(item.get("direction") or ""),
                "emphasis": tuple(str(item.get("emphasis") or "").split(","))
                if item.get("emphasis")
                else (),
            }
        )
    return result


def script_items(cases: list[dict[str, Any]]) -> list[ScriptItem]:
    return [
        ScriptItem(
            order=index,
            source_line=index,
            text=str(item["text"]),
            pronunciation=str(item.get("pronunciation") or ""),
            generated_text=str(item.get("generated_text") or item["text"]),
            direction=str(item.get("direction") or ""),
            emphasis=tuple(item.get("emphasis") or ()),
            raw_mode=bool(item.get("raw_mode", False)),
        )
        for index, item in enumerate(cases, start=1)
    ]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def prepare_database(
    settings: BenchmarkSettings,
    references: list[Path],
    reference_text: str,
    cases: list[dict[str, Any]],
) -> tuple[Database, str, str]:
    settings.ensure_runtime()
    database = Database(settings.database_path)
    database.initialize()
    voice_id = database.voices.create(
        "benchmark-reference",
        "clone-benchmark",
        "",
        source_kind="benchmark",
        project_id="benchmark",
    )
    for index, path in enumerate(references, start=1):
        database.voices.add_file(
            voice_id,
            path.name,
            path,
            path.stat().st_size,
            enabled=True,
            emotion_tag="neutral",
            reference_text=reference_text if len(references) == 1 else "",
        )
    script_path = settings.runtime_root / "cases.json"
    write_json(script_path, cases)
    script_id = database.scripts.create(
        "clone-benchmark-cases",
        script_path.name,
        script_path,
        "clone-benchmark",
        "benchmark",
        script_items(cases),
        project_id="benchmark",
    )
    return database, voice_id, script_id


def wait_for_job(
    database: Database, job_id: str, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = database.jobs.get(job_id)
        if job is None:
            raise RuntimeError(f"benchmark job disappeared: {job_id}")
        if str(job.get("status")) not in {"queued", "running"}:
            return job
        time.sleep(0.2)
    raise TimeoutError(f"任务超过 {timeout_seconds:g} 秒仍未完成")


def audio_metrics(path: Path) -> dict[str, Any]:
    try:
        import numpy as np
        import soundfile as sf

        audio, sample_rate = sf.read(path, always_2d=False)
        values = np.asarray(audio, dtype=np.float64).reshape(-1)
        if values.size == 0:
            raise ValueError("empty audio")
        peak = float(np.max(np.abs(values)))
        rms = float(np.sqrt(np.mean(np.square(values)) + 1e-12))
        return {
            "sample_rate": int(sample_rate),
            "channels": 1
            if np.asarray(audio).ndim == 1
            else int(np.asarray(audio).shape[1]),
            "duration_seconds": round(values.size / int(sample_rate), 3),
            "peak_dbfs": round(20 * np.log10(max(peak, 1e-12)), 2),
            "rms_dbfs": round(20 * np.log10(max(rms, 1e-12)), 2),
            "clipping_percent": round(float(np.mean(np.abs(values) >= 0.999)) * 100, 4),
        }
    except Exception as error:
        return {"error": type(error).__name__}


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def run_one_model(
    model_id: str,
    profiles: Profiles,
    run_root: Path,
    references: list[Path],
    reference_text: str,
    cases: list[dict[str, Any]],
    candidate_count: int,
    seed: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    profile = profiles.model(model_id)
    model_root = run_root / safe_slug(model_id)
    settings = BenchmarkSettings(PROJECT_ROOT, model_root)
    provider_config = ProviderConfigStore(settings.provider_config_path)
    result: dict[str, Any] = {
        "model_id": model_id,
        "label": str(profile.get("label") or model_id),
        "engine": str(profile.get("engine") or ""),
        "status": "pending",
        "output_root": relative_path(model_root, run_root),
    }
    engine = VoiceEngine(settings, profiles, provider_config)
    try:
        status = engine.model_available(model_id)
        result["availability"] = status.public()
        if not status.available:
            result["status"] = "skipped"
            result["reason"] = status.reason or ", ".join(status.missing_files)
            return result

        database, voice_id, script_id = prepare_database(
            settings, references, reference_text, cases
        )
        job_id = database.jobs.create(
            "clone-benchmark",
            script_id,
            voice_id,
            model_id,
            script_items(cases),
            candidate_count=candidate_count,
            reference_emotion="all",
            generation_settings=profiles.generation_settings(model_id),
            base_seed=seed,
            seed_stride=candidate_count,
            project_id="benchmark",
            created_by="clone-benchmark",
        )
        queue = JobQueue(settings, database, engine)
        sampler = GpuSampler()
        baseline = query_gpu()
        reset_torch_peaks()
        started = perf_counter()
        sampler.start()
        job: dict[str, Any] | None = None
        try:
            queue.start()
            queue.submit(job_id)
            job = wait_for_job(database, job_id, timeout_seconds)
        except Exception as error:
            result["exception"] = f"{type(error).__name__}: {error}"
            current = database.jobs.get(job_id)
            if current is not None:
                job = current
        finally:
            queue.stop()
            sampler.stop()
        elapsed = perf_counter() - started
        if job is None:
            job = database.jobs.get(job_id)
        result.update(
            {
                "status": "completed"
                if job and job.get("status") == "completed"
                else "failed",
                "job_id": job_id,
                "job_status": job.get("status") if job else "missing",
                "job_error": job.get("error", "") if job else "",
                "wall_seconds": round(elapsed, 3),
                "gpu_baseline": baseline,
                "gpu_peak": max_gpu_sample(sampler.samples),
                "gpu_samples": sampler.samples,
                "torch_memory": torch_memory(),
                "candidate_count": candidate_count,
            }
        )
        candidates = database.candidates.list_for_job(job_id)
        case_ids = {index: str(item["id"]) for index, item in enumerate(cases, start=1)}
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            audio_value = str(
                candidate.get("audio_path") or candidate.get("raw_audio_path") or ""
            )
            audio_path = Path(audio_value) if audio_value else None
            row = {
                "case_id": case_ids.get(int(candidate.get("sequence") or 0), ""),
                "sequence": candidate.get("sequence"),
                "candidate_id": candidate.get("id"),
                "candidate": candidate.get("name"),
                "seed": candidate.get("seed"),
                "text": candidate.get("text"),
                "generated_text": candidate.get("generated_text"),
                "status": candidate.get("status"),
                "elapsed_seconds": candidate.get("elapsed_seconds"),
                "duration_seconds": candidate.get("duration_seconds"),
                "error": candidate.get("error", ""),
            }
            if audio_path is not None and audio_path.is_file():
                row["audio"] = relative_path(audio_path, run_root)
                row["audio_metrics"] = audio_metrics(audio_path)
            else:
                row["audio"] = ""
                row["audio_metrics"] = {}
            rows.append(row)
        result["candidates"] = rows
        write_json(model_root / "result.json", result)
        return result
    finally:
        try:
            engine.unload()
        except Exception:
            pass


def max_gpu_sample(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not samples:
        return None
    return max(samples, key=lambda item: int(item.get("memory_used_mib", 0)))


def write_review_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "model_id",
        "model_label",
        "case_id",
        "sequence",
        "candidate",
        "seed",
        "audio",
        "speaker_similarity",
        "pronunciation",
        "naturalness",
        "emotion",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            for item in result.get("candidates", []):
                if not item.get("audio"):
                    continue
                writer.writerow(
                    {
                        "model_id": result.get("model_id", ""),
                        "model_label": result.get("label", ""),
                        "case_id": item.get("case_id", ""),
                        "sequence": item.get("sequence", ""),
                        "candidate": item.get("candidate", ""),
                        "seed": item.get("seed", ""),
                        "audio": item.get("audio", ""),
                        "speaker_similarity": "",
                        "pronunciation": "",
                        "naturalness": "",
                        "emotion": "",
                        "notes": "",
                    }
                )


def write_report(path: Path, results: list[dict[str, Any]]) -> None:
    cards: list[str] = []
    for result in results:
        rows: list[str] = []
        for item in result.get("candidates", []):
            audio = str(item.get("audio") or "")
            if not audio:
                continue
            metrics = item.get("audio_metrics") or {}
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('case_id') or ''))}</td>"
                f"<td>{html.escape(str(item.get('candidate') or ''))}</td>"
                f"<td>{html.escape(str(item.get('status') or ''))}</td>"
                f"<td>{html.escape(str(item.get('elapsed_seconds') or ''))}</td>"
                f"<td>{html.escape(str(metrics.get('duration_seconds') or ''))}</td>"
                f"<td><audio controls preload='none' src='{html.escape(audio)}'></audio></td>"
                "</tr>"
            )
        status = html.escape(str(result.get("status") or ""))
        error = html.escape(str(result.get("job_error") or result.get("reason") or ""))
        cards.append(
            "<section>"
            f"<h2>{html.escape(str(result.get('label') or result.get('model_id') or ''))}"
            f" <small>({status})</small></h2>"
            f"<p>{error}</p>"
            "<table><thead><tr><th>用例</th><th>候选</th><th>状态</th>"
            "<th>生成秒数</th><th>音频秒数</th><th>试听</th></tr></thead>"
            f"<tbody>{''.join(rows) or '<tr><td colspan=6>没有可试听输出</td></tr>'}</tbody></table>"
            "</section>"
        )
    content = """<!doctype html>
<meta charset="utf-8">
<title>Voice Lab clone benchmark</title>
<style>
body{font:15px system-ui,sans-serif;max-width:1300px;margin:24px auto;padding:0 16px;background:#fafafa;color:#222}
section{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin:16px 0}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:middle}
th{background:#f3f3f3}audio{width:280px}small{font-weight:normal;color:#666}
</style>
""" + "\n".join(
        cards
    )
    path.write_text(content, encoding="utf-8")


def preflight(model_ids: list[str]) -> int:
    settings = BenchmarkSettings(
        PROJECT_ROOT, PROJECT_ROOT / "output" / ".clone-preflight"
    )
    profiles = Profiles.load(settings.profiles_path)
    engine = VoiceEngine(
        settings, profiles, ProviderConfigStore(settings.provider_config_path)
    )
    payload: dict[str, Any] = {
        "gpu": query_gpu(),
        "models": {},
    }
    try:
        for model_id in model_ids:
            try:
                payload["models"][model_id] = engine.model_available(model_id).public()
            except (KeyError, ValueError, RuntimeError) as error:
                payload["models"][model_id] = {
                    "available": False,
                    "reason": f"{type(error).__name__}: {error}",
                }
    finally:
        engine.unload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 Voice Lab 单 JobQueue 串行运行本地声音克隆对比测试。"
    )
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        help="模型 ID，可重复指定；默认运行五个本地代表配置",
    )
    parser.add_argument(
        "--reference",
        action="append",
        help="参考音频路径（项目目录内，可重复指定；默认使用已有 validation/reference.wav）",
    )
    parser.add_argument("--reference-text", default="", help="单条参考音频的准确逐字稿")
    parser.add_argument("--case-file", type=Path, help="UTF-8 JSON 测试用例数组")
    parser.add_argument("--include-raw", action="store_true", help="追加一条 raw 音素兼容性用例")
    parser.add_argument("--max-cases", type=int, default=6)
    parser.add_argument("--candidate-count", type=int, default=2, choices=(1, 2, 3, 4))
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--timeout", type=float, default=900.0, help="每个模型任务的最长秒数")
    parser.add_argument("--output-root", type=Path, help="输出根目录（必须在项目目录内）")
    parser.add_argument("--preflight", action="store_true", help="只检查显卡和模型资源，不加载模型")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_ids = list(dict.fromkeys(args.models or DEFAULT_MODELS))
    if args.preflight:
        return preflight(model_ids)
    if args.max_cases < 1:
        raise SystemExit("--max-cases 必须大于 0")
    if args.timeout <= 0:
        raise SystemExit("--timeout 必须大于 0")
    references = [
        resolve_project_path(value)
        for value in (args.reference or [str(path) for path in default_references()])
    ]
    missing = [str(path) for path in references if not path.is_file()]
    if missing:
        raise SystemExit("参考音频不存在:\n" + "\n".join(missing))
    if args.reference_text.strip() and len(references) != 1:
        raise SystemExit("提供 --reference-text 时只能指定一条参考音频")
    cases = load_cases(
        resolve_project_path(args.case_file) if args.case_file else None,
        bool(args.include_raw),
    )[: args.max_cases]
    timestamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    requested_root = (
        resolve_project_path(args.output_root)
        if args.output_root
        else PROJECT_ROOT / "output" / "clone-benchmark" / timestamp
    )
    run_root = requested_root
    suffix = 1
    while run_root.exists():
        run_root = requested_root.with_name(f"{requested_root.name}-{suffix}")
        suffix += 1
    run_root.mkdir(parents=True, exist_ok=False)
    write_json(run_root / "cases.json", cases)
    write_json(
        run_root / "run.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "models": model_ids,
            "references": [str(path) for path in references],
            "reference_text_provided": bool(args.reference_text.strip()),
            "candidate_count": args.candidate_count,
            "seed": args.seed,
            "hardware_before": query_gpu(),
        },
    )
    profiles = Profiles.load(PROJECT_ROOT / "web" / "config" / "profiles.json")
    results: list[dict[str, Any]] = []
    for index, model_id in enumerate(model_ids, start=1):
        print(f"[{index}/{len(model_ids)}] 开始 {model_id}", flush=True)
        try:
            result = run_one_model(
                model_id,
                profiles,
                run_root,
                references,
                args.reference_text,
                cases,
                args.candidate_count,
                args.seed,
                args.timeout,
            )
        except Exception as error:
            result = {
                "model_id": model_id,
                "status": "failed",
                "exception": f"{type(error).__name__}: {error}",
            }
        results.append(result)
        write_json(run_root / "results.json", results)
        print(
            f"[{index}/{len(model_ids)}] {model_id}: {result.get('status')}"
            + (
                f" ({result.get('job_error') or result.get('reason')})"
                if result.get("job_error") or result.get("reason")
                else ""
            ),
            flush=True,
        )
    write_review_csv(run_root / "review.csv", results)
    write_report(run_root / "report.html", results)
    write_json(run_root / "results.json", results)
    print(f"测试完成，试听页: {run_root / 'report.html'}", flush=True)
    return 0 if any(item.get("status") == "completed" for item in results) else 1


if __name__ == "__main__":
    main()
