from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import FakeEngine, login_as_admin, make_wav_bytes, wait_for_job


def test_script_is_independent_and_generation_compares_selected_voices(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )
    with TestClient(application) as client:
        login_as_admin(client, application.state.services)
        project = client.post(
            "/api/projects", json={"name": "voice comparison", "description": ""}
        ).json()["project"]
        voices = []
        for name in ("voice one", "voice two"):
            response = client.post(
                "/api/voices",
                data={"project_id": project["id"], "name": name},
                files={"files": (f"{name}.wav", make_wav_bytes(), "audio/wav")},
            )
            assert response.status_code == 201
            voices.append(response.json()["voice"])

        uploaded = client.post(
            "/api/scripts",
            data={"project_id": project["id"]},
            files={"file": ("comparison.txt", "台词 | mo-la\n", "text/plain")},
        )
        assert uploaded.status_code == 201
        script = uploaded.json()["script"]
        assert "default_voice_id" not in script

        created = client.post(
            "/api/jobs",
            data={
                "project_id": project["id"],
                "script_id": script["id"],
                "voice_ids": json.dumps([voice["id"] for voice in voices]),
                "model_id": "test_model",
                "base_seed": "77",
            },
        )
        assert created.status_code == 201
        jobs = [wait_for_job(client, job["id"]) for job in created.json()["jobs"]]

    assert len(jobs) == 2
    assert {job["voice_name"] for job in jobs} == {"voice one", "voice two"}
    assert all(job["model_id"] == "test_model" for job in jobs)
    assert [candidate["seed"] for candidate in jobs[0]["items"][0]["candidates"]] == [
        77,
        78,
    ]
    assert [candidate["seed"] for candidate in jobs[1]["items"][0]["candidates"]] == [
        77,
        78,
    ]
