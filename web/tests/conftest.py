from __future__ import annotations

import io
import json
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


WEB_ROOT = Path(__file__).resolve().parents[1]
if str(WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_ROOT))

from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402


def make_wav_bytes(duration_seconds: float = 0.08, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * int(duration_seconds * sample_rate))
    return buffer.getvalue()


def make_encoded_audio_bytes(container_format: str, codec: str) -> bytes:
    import av
    import numpy as np

    buffer = io.BytesIO()
    with av.open(buffer, "w", format=container_format) as output:
        stream = output.add_stream(codec, rate=16_000)
        stream.layout = "mono"
        samples = np.zeros((1, 1_600), dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = 16_000
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return buffer.getvalue()


class FakeEngine:
    def __init__(
        self,
        settings: Settings,
        profiles: object,
        provider_config: object | None = None,
    ) -> None:
        del provider_config
        self.settings = settings
        self.generate_calls = 0
        self.generation_settings_calls: list[dict[str, float | int]] = []
        self.seed_calls: list[int] = []
        self.output_paths: list[Path] = []

    def model_status(self) -> dict[str, object]:
        return {"loaded": True, "missing_models": []}

    def model_available(self, model_id: str) -> SimpleNamespace:
        del model_id
        return SimpleNamespace(available=True, reason="")

    def prepare_reference(
        self, voice_files: list[dict[str, object]], target_path: Path
    ) -> SimpleNamespace:
        if not voice_files:
            raise ValueError("missing reference")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(make_wav_bytes())
        return SimpleNamespace(
            path=target_path, prompt_text="", prompt_lang="", duration_seconds=0.08
        )

    def generate(
        self,
        item: dict[str, object],
        reference: object,
        model_id: str,
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int] | None = None,
    ) -> SimpleNamespace:
        del item, reference, model_id
        self.generate_calls += 1
        self.generation_settings_calls.append(generation_settings or {})
        self.seed_calls.append(seed)
        self.output_paths.append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = make_wav_bytes()
        output_path.write_bytes(payload)
        return SimpleNamespace(
            audio_path=output_path,
            duration_seconds=0.08,
            elapsed_seconds=0.25,
            processing_backend="gpt-sovits-v2",
        )


@pytest.fixture
def settings_factory(tmp_path: Path):
    counter = 0

    def create() -> Settings:
        nonlocal counter
        counter += 1
        settings = Settings(tmp_path / f"workspace-{counter}")
        settings.static_root.mkdir(parents=True, exist_ok=True)
        settings.config_root.mkdir(parents=True, exist_ok=True)
        (settings.static_root / "index.html").write_text(
            "<!doctype html><title>test</title>", encoding="utf-8"
        )
        (settings.static_root / "admin.html").write_text(
            "<!doctype html><title>admin test</title>", encoding="utf-8"
        )
        settings.profiles_path.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "id": "test_model",
                            "label": "Test",
                            "description": "",
                            "clone_overrides": {
                                "temperature": 0.72,
                                "top_p": 0.88,
                                "repetition_penalty": 1.28,
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return settings

    return create


@pytest.fixture
def app_client(settings_factory):
    settings = settings_factory()
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)
    with TestClient(application) as client:
        login_as_admin(client, application.state.services)
        yield client, application.state.services


def login_as_admin(client: TestClient, services: object) -> dict[str, object]:
    password = services.auth.bootstrap_password
    if not password:
        raise AssertionError("fresh test application did not expose bootstrap password")
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": password},
    )
    assert response.status_code == 200
    return response.json()["user"]


def wait_for_job(
    client: TestClient, job_id: str, timeout: float = 3.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {job_id}")


def wait_for_candidate(
    client: TestClient,
    job_id: str,
    item_id: str,
    candidate_id: str,
    timeout: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        item = next(row for row in job["items"] if row["id"] == item_id)
        candidate = next(row for row in item["candidates"] if row["id"] == candidate_id)
        if candidate["status"] not in {"queued", "running"}:
            return candidate
        time.sleep(0.02)
    raise AssertionError(f"candidate did not finish: {candidate_id}")
