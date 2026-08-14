from __future__ import annotations

import io
import json
import re
import sqlite3
import wave
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import (
    FakeEngine,
    make_encoded_audio_bytes,
    make_wav_bytes,
    wait_for_candidate,
    wait_for_job,
)


def test_app_factory_is_lazy(settings_factory) -> None:
    settings = settings_factory()

    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    assert application.title == "Voice Lab"
    assert not settings.database_path.exists()


def test_identity_rejects_path_cookie(app_client) -> None:
    client, _ = app_client
    client.cookies.set("voice_lab_client", "../../outside")

    response = client.get("/api/identity")

    assert response.status_code == 200
    assert re.fullmatch(r"guest-[0-9a-f]{16}", response.json()["client_id"])


def test_invalid_wav_rolls_back_voice(app_client) -> None:
    client, services = app_client
    before = services.database.monitoring.counts()["voices"]

    response = client.post(
        "/api/voices",
        data={"name": "broken"},
        files=[("files", ("broken.wav", b"not wav", "audio/wav"))],
    )

    assert response.status_code == 422
    assert services.database.monitoring.counts()["voices"] == before
    assert not any(services.settings.voice_upload_root.rglob("*.wav"))


def test_mp3_and_m4a_are_normalized_to_inference_wav(app_client) -> None:
    client, services = app_client

    response = client.post(
        "/api/voices",
        data={"name": "phone recordings"},
        files=[
            (
                "files",
                ("iphone.m4a", make_encoded_audio_bytes("ipod", "aac"), "audio/mp4"),
            ),
            (
                "files",
                ("recorder.mp3", make_encoded_audio_bytes("mp3", "mp3"), "audio/mpeg"),
            ),
        ],
    )

    assert response.status_code == 201
    voice_id = response.json()["voice"]["id"]
    detail = client.get(f"/api/voices/{voice_id}").json()["voice"]
    assert {item["original_name"] for item in detail["files"]} == {
        "iphone.m4a",
        "recorder.mp3",
    }
    stored = services.database.voices.list_files(voice_id)
    assert len(stored) == 2
    for item in stored:
        path = Path(item["source_path"])
        assert path.suffix == ".wav"
        with wave.open(str(path), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == 48_000


def test_unsupported_voice_extension_rolls_back_voice(app_client) -> None:
    client, services = app_client
    before = services.database.monitoring.counts()["voices"]

    response = client.post(
        "/api/voices",
        data={"name": "unsupported"},
        files=[("files", ("notes.txt", b"not audio", "text/plain"))],
    )

    assert response.status_code == 422
    assert "MP3" in response.json()["detail"]
    assert services.database.monitoring.counts()["voices"] == before


def test_decoded_voice_duration_limit_rolls_back_upload(
    app_client, monkeypatch
) -> None:
    client, services = app_client
    before = services.database.monitoring.counts()["voices"]
    monkeypatch.setattr("app.audio_conversion.MAX_NORMALIZED_SAMPLES", 10)

    response = client.post(
        "/api/voices",
        data={"name": "too long"},
        files=[("files", ("long.wav", make_wav_bytes(), "audio/wav"))],
    )

    assert response.status_code == 422
    assert "不能超过" in response.json()["detail"]
    assert services.database.monitoring.counts()["voices"] == before


def test_clone_only_upload_queue_play_and_download_workflow(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "tester", 2)
    script = _create_script(
        client,
        "script.txt",
        "第一句 | mo-la\n第二句 | GU-la。↘\n",
    )

    response = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    )
    assert response.status_code == 201
    queued = response.json()["job"]
    assert queued["status"] == "queued"
    job = wait_for_job(client, queued["id"])

    assert job["status"] == "completed"
    assert job["output_type"] == "gpt_sovits_raw"
    assert [item["text"] for item in job["items"]] == ["第一句", "第二句"]
    assert all(len(item["candidates"]) == 2 for item in job["items"])
    assert services.engine.generate_calls == 4
    assert all(call == {} for call in services.engine.generation_settings_calls)
    assert "effect_id" not in job
    assert "effect_settings" not in job
    assert "variants" not in job
    assert "can_reprocess" not in job

    for item in job["items"]:
        for candidate in item["candidates"]:
            assert candidate["audio_url"]
            assert "raw_audio_url" not in candidate
            assert "matched_audio_url" not in candidate
            stored = services.database.candidates.get(candidate["id"])
            assert stored["audio_path"] == stored["raw_audio_path"]

    root = services.settings.job_root / job["id"]
    assert sorted(path.name for path in (root / "audio").glob("*.wav")) == [
        "001.wav",
        "002.wav",
    ]
    assert not (root / "final").exists()

    audio = client.get(job["items"][0]["audio_url"])
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    archive = client.get(job["download_url"])
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        assert package.namelist() == ["001.wav", "002.wav", "台本与发音.json"]

    config = client.get("/api/config").json()
    assert [model["id"] for model in config["models"]] == ["test_model"]
    assert "effects" not in config
    assert "postprocess_controls" not in config
    assert "不做降噪" in config["output_description"]
    assert "postprocess_queue" not in client.get("/api/health").json()


def test_regenerate_accept_and_export_clone_candidate(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "candidate voice")
    script = _create_script(client, "candidate.txt", "原台词 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 3,
        },
    ).json()["job"]
    job = wait_for_job(client, created["id"])
    item = job["items"][0]
    source = item["candidates"][0]

    assert len(item["candidates"]) == 3
    assert len({candidate["seed"] for candidate in item["candidates"]}) == 3
    regenerated_response = client.post(
        f"/api/jobs/{job['id']}/items/{item['id']}/regenerate",
        json={
            "name": "强调版",
            "text": "修改后的台词",
            "pronunciation": "GU-la。↘",
            "direction": "auto",
            "source_candidate_id": source["id"],
            "seed": 123456,
            "generation_settings": {
                "temperature": 0.65,
                "speed_factor": 0.92,
                "top_k": 18,
                "top_p": 0.85,
                "repetition_penalty": 1.3,
            },
        },
    )
    assert regenerated_response.status_code == 201
    regenerated_id = regenerated_response.json()["candidate"]["id"]
    regenerated = wait_for_candidate(client, job["id"], item["id"], regenerated_id)

    assert regenerated["status"] == "completed"
    assert regenerated["source_candidate_id"] == source["id"]
    assert regenerated["pronunciation"] == "GU-la。↘"
    assert regenerated["direction"] == "fall"
    assert regenerated["emphasis"] == ["gu"]
    assert (
        services.engine.generation_settings_calls[-1]
        == regenerated["generation_settings"]
    )
    stored = services.database.candidates.get(regenerated_id)
    assert stored["audio_path"] == stored["raw_audio_path"]

    accepted = client.post(
        f"/api/jobs/{job['id']}/items/{item['id']}/accept",
        json={"candidate_id": regenerated_id},
    )
    assert accepted.status_code == 200
    accepted_job = accepted.json()["job"]
    assert accepted_job["items"][0]["accepted_candidate_id"] == regenerated_id

    export = client.get(accepted_job["export_url"])
    assert export.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export.content)) as package:
        assert package.namelist() == ["Audio/001.wav", "UnityAudioManifest.json"]
        manifest = json.loads(package.read("UnityAudioManifest.json"))
    assert manifest["items"][0]["candidateId"] == regenerated_id
    assert manifest["items"][0]["text"] == "修改后的台词"

    assert client.post(f"/api/jobs/{job['id']}/variants", json={}).status_code == 404
    assert (
        client.post(
            f"/api/jobs/{job['id']}/items/{item['id']}/candidates/{regenerated_id}/tune",
            json={},
        ).status_code
        == 404
    )


def test_regenerate_rejects_invalid_generation_settings(app_client) -> None:
    client, _ = app_client
    voice = _create_voice(client, "settings voice")
    script = _create_script(client, "settings.txt", "台词 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    ).json()["job"]
    job = wait_for_job(client, created["id"])
    item = job["items"][0]

    response = client.post(
        f"/api/jobs/{job['id']}/items/{item['id']}/regenerate",
        json={
            "text": item["text"],
            "pronunciation": item["pronunciation"],
            "generation_settings": {"temperature": 99},
        },
    )

    assert response.status_code == 422
    assert "表现变化" in response.json()["detail"]


def test_historical_dsp_candidates_are_hidden_and_raw_audio_is_served(
    app_client,
) -> None:
    client, services = app_client
    voice = _create_voice(client, "historical voice")
    script = _create_script(client, "historical.txt", "台词 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    ).json()["job"]
    job = wait_for_job(client, created["id"])
    item = job["items"][0]
    first, second = item["candidates"]
    raw_path = services.settings.job_root / job["id"] / "historical-raw.wav"
    processed_path = services.settings.job_root / job["id"] / "historical-dsp.wav"
    raw_bytes = make_wav_bytes(0.04)
    processed_bytes = make_wav_bytes(0.12)
    raw_path.write_bytes(raw_bytes)
    processed_path.write_bytes(processed_bytes)
    with sqlite3.connect(services.settings.database_path) as connection:
        connection.execute(
            "UPDATE job_item_candidates SET raw_audio_path=?, audio_path=? WHERE id=?",
            (str(raw_path), str(processed_path), first["id"]),
        )
        connection.execute(
            "UPDATE job_item_candidates SET kind='dsp' WHERE id=?",
            (second["id"],),
        )
        connection.execute(
            "UPDATE job_items SET raw_audio_path=?, audio_path=? WHERE id=?",
            (str(raw_path), str(processed_path), item["id"]),
        )

    detail = client.get(f"/api/jobs/{job['id']}").json()["job"]
    assert [candidate["id"] for candidate in detail["items"][0]["candidates"]] == [
        first["id"]
    ]
    assert client.get(first["audio_url"]).content == raw_bytes
    assert client.get(detail["items"][0]["audio_url"]).content == raw_bytes


def test_jobs_are_private_and_local_admin_can_access_all(
    settings_factory, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.api.access._local_admin_hosts",
        lambda: frozenset({"127.0.0.1", "::1", "192.168.87.66"}),
    )
    settings = settings_factory()
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    with TestClient(application) as owner, TestClient(application) as stranger:
        owner_id = owner.get("/api/identity").json()["client_id"]
        assert owner_id != stranger.get("/api/identity").json()["client_id"]
        voice = _create_voice(owner, "owner voice")
        script = _create_script(owner, "private.txt", "私有任务 | mo-la\n")
        created = owner.post(
            "/api/jobs",
            data={
                "voice_id": voice["id"],
                "model_id": "test_model",
                "script_id": script["id"],
            },
        ).json()["job"]
        job = wait_for_job(owner, created["id"])
        item = job["items"][0]
        candidate = item["candidates"][0]

        assert stranger.get("/api/jobs").json()["jobs"] == []
        for path in (
            f"/api/jobs/{job['id']}",
            item["audio_url"],
            job["download_url"],
            job["export_url"],
            candidate["audio_url"],
        ):
            assert stranger.get(path).status_code == 404
        assert (
            stranger.post(
                f"/api/jobs/{job['id']}/items/{item['id']}/accept",
                json={"candidate_id": candidate["id"]},
            ).status_code
            == 404
        )
        renamed = owner.patch(f"/api/jobs/{job['id']}", json={"name": "我的新名称"})
        assert renamed.status_code == 200

        assert stranger.get("/admin").status_code == 403
        with TestClient(application, client=("127.0.0.1", 50100)) as admin:
            admin_jobs = admin.get("/api/admin/jobs").json()["jobs"]
            assert [row["id"] for row in admin_jobs] == [job["id"]]
            detail = admin.get(f"/api/admin/jobs/{job['id']}").json()["job"]
            assert detail["client_id"] == owner_id
            assert admin.get(detail["items"][0]["audio_url"]).status_code == 200
        with TestClient(application, client=("192.168.87.66", 50101)) as lan_admin:
            assert lan_admin.get("/api/identity").json()["admin_available"] is True
        with TestClient(application, client=("192.168.87.67", 50102)) as lan_user:
            assert lan_user.get("/api/identity").json()["admin_available"] is False


def test_docker_admin_bootstrap_token_sets_cookie(
    settings_factory, monkeypatch
) -> None:
    monkeypatch.setenv("VOICE_LAB_ADMIN_TOKEN", "local-docker-token")
    monkeypatch.setattr("app.api.access._local_admin_hosts", lambda: frozenset())
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application, client=("172.20.0.2", 50100)) as client:
        assert client.get("/admin").status_code == 403
        assert client.get("/admin?admin_key=wrong").status_code == 403
        assert client.get("/admin?admin_key=local-docker-token").status_code == 200
        assert client.get("/api/identity").json()["admin_available"] is True


def test_voice_library_owner_can_edit_but_other_users_cannot(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application) as owner, TestClient(application) as stranger:
        owner.get("/api/identity")
        stranger.get("/api/identity")
        voice = _create_voice(owner, "source", 2)
        voice_id = voice["id"]
        stranger_detail = stranger.get(f"/api/voices/{voice_id}").json()["voice"]
        assert stranger_detail["can_edit"] is False
        file_id = stranger_detail["files"][0]["id"]
        assert (
            stranger.patch(
                f"/api/voices/{voice_id}", json={"name": "blocked", "notes": ""}
            ).status_code
            == 403
        )
        assert (
            stranger.patch(
                f"/api/voices/{voice_id}/files/{file_id}", json={"enabled": False}
            ).status_code
            == 403
        )

        updated = owner.patch(
            f"/api/voices/{voice_id}",
            json={"name": "renamed source", "notes": "after"},
        )
        assert updated.status_code == 200
        appended = owner.post(
            f"/api/voices/{voice_id}/files",
            files=[("files", ("three.wav", make_wav_bytes(), "audio/wav"))],
        )
        assert appended.status_code == 201
        assert appended.json()["voice"]["file_count"] == 3


def test_voice_file_update_validates_before_mutating(app_client) -> None:
    client, _ = app_client
    voice = _create_voice(client, "atomic source", 2)
    detail = client.get(f"/api/voices/{voice['id']}").json()["voice"]
    file_id = detail["files"][0]["id"]

    response = client.patch(
        f"/api/voices/{voice['id']}/files/{file_id}",
        json={"enabled": False, "emotion_tag": "unknown"},
    )

    assert response.status_code == 422
    refreshed = client.get(f"/api/voices/{voice['id']}").json()["voice"]
    stored = next(item for item in refreshed["files"] if item["id"] == file_id)
    assert stored["enabled"] is True
    assert stored["emotion_tag"] == "neutral"


def _create_voice(client: TestClient, name: str, count: int = 1) -> dict:
    response = client.post(
        "/api/voices",
        data={"name": name},
        files=[
            ("files", (f"source-{index}.wav", make_wav_bytes(), "audio/wav"))
            for index in range(count)
        ],
    )
    assert response.status_code == 201
    return response.json()["voice"]


def _create_script(client: TestClient, name: str, text: str) -> dict:
    response = client.post(
        "/api/scripts",
        files={"file": (name, text.encode(), "text/plain")},
    )
    assert response.status_code == 201
    return response.json()["script"]
