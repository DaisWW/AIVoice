from __future__ import annotations

import io
import json
import hashlib
import os
import sqlite3
import time
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.candidate_operations import _directional_text
from app.api.downloads import JobDownloadService
from app.auth import verify_password
from app.main import create_app
from conftest import (
    FakeEngine,
    login_as_admin,
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


def test_identity_ignores_legacy_guest_cookie(app_client) -> None:
    client, _ = app_client
    client.cookies.set("voice_lab_client", "../../outside")

    response = client.get("/api/identity")

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "admin"


def test_password_verification_rejects_excessive_scrypt_memory(monkeypatch) -> None:
    called = False

    def unexpected_scrypt(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("scrypt should not run for an oversized record")

    monkeypatch.setattr(hashlib, "scrypt", unexpected_scrypt)
    encoded = "scrypt$1048576$32$1$" + "00" * 16 + "$" + "00" * 32

    assert not verify_password("password", encoded)
    assert not called


def test_health_does_not_expose_engine_paths(app_client) -> None:
    client, services = app_client
    services.engine.model_status = lambda: {
        "loaded": False,
        "missing_models": [r"C:\\server\\models\\secret.safetensors"],
        "models": {
            "test_model": {
                "id": "test_model",
                "label": "Test",
                "available": False,
                "loaded": False,
                "missing_files": [r"C:\\server\\models\\secret.safetensors"],
                "availability_reason": "C:\\server\\private",
            }
        },
    }

    response = client.get("/api/health")

    assert response.status_code == 200
    engine = response.json()["engine"]
    assert r"C:\\server" not in response.text
    assert engine["unavailable_models"] == ["test_model"]
    assert engine["models"]["test_model"] == {
        "id": "test_model",
        "label": "Test",
        "available": False,
        "loaded": False,
    }


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
    expected_settings = {
        "temperature": 0.72,
        "speed_factor": 1.0,
        "top_k": 15,
        "top_p": 0.88,
        "repetition_penalty": 1.28,
    }
    assert all(
        candidate["generation_settings"] == expected_settings
        for item in job["items"]
        for candidate in item["candidates"]
    )
    assert all(
        call == expected_settings for call in services.engine.generation_settings_calls
    )
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
    assert config["models"][0]["generation_defaults"] == expected_settings
    assert "effects" not in config
    assert "postprocess_controls" not in config
    assert "不做降噪" in config["output_description"]
    assert "postprocess_queue" not in client.get("/api/health").json()


def test_job_items_snapshot_saved_rewrite_instructions(app_client) -> None:
    client, _ = app_client
    voice = _create_voice(client, "snapshot instructions")
    script = _create_script(
        client,
        "snapshot-instructions.txt",
        "第一句 | mo-la\n第二句 | gu-la\n",
    )
    detail = client.get(f"/api/scripts/{script['id']}").json()["script"]
    saved = client.put(
        f"/api/scripts/{script['id']}/items",
        json={
            "version": detail["version"],
            "items": [
                {
                    "text": "第一句",
                    "pronunciation": "mo-la",
                    "rewrite_instruction": "第一句要克制",
                },
                {
                    "text": "第二句",
                    "pronunciation": "gu-la",
                    "rewrite_instruction": "第二句要更响亮",
                },
            ],
        },
    )
    assert saved.status_code == 200

    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    )
    assert created.status_code == 201
    job = wait_for_job(client, created.json()["job"]["id"])

    assert [item["rewrite_instruction"] for item in job["items"]] == [
        "第一句要克制",
        "第二句要更响亮",
    ]
    assert [
        candidate["rewrite_instruction"]
        for item in job["items"]
        for candidate in item["candidates"]
    ] == ["第一句要克制", "第一句要克制", "第二句要更响亮", "第二句要更响亮"]
    archive = client.get(job["download_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        manifest = json.loads(package.read("台本与发音.json"))
    assert [item["rewriteInstruction"] for item in manifest] == [
        "第一句要克制",
        "第二句要更响亮",
    ]


def test_download_endpoints_reject_corrupt_database_numbers(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "corrupt download voice")
    script = _create_script(client, "corrupt-download.txt", "第一句 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    )
    assert created.status_code == 201
    job = wait_for_job(client, created.json()["job"]["id"])
    item = job["items"][0]

    with sqlite3.connect(services.settings.database_path) as connection:
        connection.execute(
            "UPDATE jobs SET total_items='broken' WHERE id=?", (job["id"],)
        )
        connection.commit()
    assert client.get(f"/api/jobs/{job['id']}/export").status_code == 409

    with sqlite3.connect(services.settings.database_path) as connection:
        connection.execute("UPDATE jobs SET total_items=1 WHERE id=?", (job["id"],))
        connection.execute(
            "UPDATE job_items SET sequence='broken' WHERE id=?", (item["id"],)
        )
        connection.commit()
    assert (
        client.get(f"/api/jobs/{job['id']}/items/{item['id']}/download").status_code
        == 409
    )


def test_audio_response_snapshots_before_source_is_removed(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "snapshot voice")
    script = _create_script(client, "snapshot.txt", "快照台词 | mo-la\n")
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
    source = Path(services.database.jobs.item(job["id"], item["id"])["audio_path"])
    expected = source.read_bytes()

    response = JobDownloadService(services).item_response(job, item["id"])
    snapshot = Path(response.path)
    source.unlink()

    try:
        assert snapshot.read_bytes() == expected
    finally:
        snapshot.unlink(missing_ok=True)


def test_stale_download_snapshots_are_cleaned_on_startup(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    stale = settings.export_root / ".download-stale.zip"
    fresh = settings.export_root / ".download-fresh.zip"
    unrelated = settings.export_root / "keep.zip"
    stale.write_bytes(b"stale")
    fresh.write_bytes(b"fresh")
    unrelated.write_bytes(b"keep")
    old_time = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (old_time, old_time))

    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)
    with TestClient(application):
        assert not stale.exists()
        assert fresh.exists()
        assert unrelated.exists()


def test_regenerate_rejects_active_job_or_item(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "active regenerate voice")
    script = _create_script(client, "active-regenerate.txt", "原台词 | mo-la\n")
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
    with sqlite3.connect(services.settings.database_path) as connection:
        connection.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
        connection.execute(
            "UPDATE job_items SET status='running' WHERE id=?", (item["id"],)
        )
        connection.commit()

    response = client.post(
        f"/api/jobs/{job['id']}/items/{item['id']}/regenerate",
        json={"text": "重做", "pronunciation": "mo-la"},
    )

    assert response.status_code == 409
    assert "处理完成后" in response.json()["detail"]


def test_script_audio_export_rejects_corrupt_item_count(app_client) -> None:
    client, services = app_client
    _create_voice(client, "corrupt script voice")
    script = _create_script(client, "corrupt-script.txt", "第一句 | mo-la\n")
    with sqlite3.connect(services.settings.database_path) as connection:
        connection.execute(
            "UPDATE scripts SET item_count='broken' WHERE id=?", (script["id"],)
        )
        connection.commit()

    response = client.get(f"/api/scripts/{script['id']}/audio-export?scope=accepted")

    assert response.status_code == 409


@pytest.mark.parametrize("identifier", ("../escape", "C:\\escape", "bad/id"))
def test_archive_paths_reject_unsafe_ids(app_client, identifier: str) -> None:
    _, services = app_client

    with pytest.raises(HTTPException) as error:
        JobDownloadService(services)._archive_path(identifier, ".zip")
    assert error.value.status_code == 409


def test_script_library_keeps_voice_selection_in_generation(app_client) -> None:
    client, _ = app_client
    second_voice = _create_voice(client, "second voice")
    uploaded = client.post(
        "/api/scripts",
        files={"file": ("bound.txt", "绑定台词 | mo-la\n", "text/plain")},
    )

    assert uploaded.status_code == 201
    script = uploaded.json()["script"]
    assert "default_voice_id" not in script
    detail = client.get(f"/api/scripts/{script['id']}").json()["script"]
    assert detail["items"][0]["text"] == "绑定台词"

    updated = client.patch(
        f"/api/scripts/{script['id']}",
        json={"name": "已配置台本"},
    )
    assert updated.status_code == 200
    assert updated.json()["script"]["name"] == "已配置台本"

    created = client.post(
        "/api/jobs",
        data={
            "model_id": "test_model",
            "script_id": script["id"],
            "voice_id": second_voice["id"],
        },
    )
    assert created.status_code == 201
    assert created.json()["job"]["voice_id"] == second_voice["id"]


def test_script_items_round_trip_export_import_and_edit(app_client) -> None:
    client, services = app_client
    _create_voice(client, "script editor voice")
    script = _create_script(client, "可编辑台本.txt", "第一句 | mo-la\n第二句 | gu-la\n")

    updated = client.put(
        f"/api/scripts/{script['id']}/items",
        json={
            "items": [
                {"text": "改过的第一句", "pronunciation": "mo-la"},
                {"text": "第二句", "pronunciation": "GU-la。↘"},
            ]
        },
    )
    assert updated.status_code == 200
    assert updated.json()["script"]["items"][0]["text"] == "改过的第一句"

    exported = client.get(f"/api/scripts/{script['id']}/export")
    assert exported.status_code == 200
    assert "filename*=UTF-8''" in exported.headers["content-disposition"]
    assert exported.text.splitlines() == [
        "text,pronunciation",
        "改过的第一句,mo-la",
        "第二句,GU-la。↘",
    ]

    imported = client.post(
        f"/api/scripts/{script['id']}/import",
        files={"file": ("round-trip.csv", exported.content, "text/csv")},
    )
    assert imported.status_code == 200
    detail = client.get(f"/api/scripts/{script['id']}").json()["script"]
    assert [item["text"] for item in detail["items"]] == ["改过的第一句", "第二句"]
    assert services.database.scripts.get(script["id"])["item_count"] == 2


def test_script_and_job_delete_preserve_voice_library(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "deletion voice")
    script = _create_script(client, "deletion.txt", "要删除的台词 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "model_id": "test_model",
            "script_id": script["id"],
            "voice_id": voice["id"],
        },
    )
    assert created.status_code == 201
    job = wait_for_job(client, created.json()["job"]["id"])
    job_root = services.settings.job_root / job["id"]

    blocked = client.delete(f"/api/scripts/{script['id']}")
    assert blocked.status_code == 409
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not job_root.exists()
    assert client.delete(f"/api/scripts/{script['id']}").status_code == 204
    assert services.database.voices.get(voice["id"]) is not None


def test_delete_single_job_item_removes_audio_and_job(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "single item deletion voice")
    script = _create_script(client, "single-item.txt", "要删除的台词 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": "2",
        },
    ).json()["job"]
    job = wait_for_job(client, created["id"])
    item = job["items"][0]
    paths = [
        Path(services.database.jobs.item(created["id"], item["id"])[field])
        for field in ("audio_path", "raw_audio_path")
        if services.database.jobs.item(created["id"], item["id"])[field]
    ]
    paths.extend(
        Path(services.database.candidates.get(candidate["id"])[field])
        for candidate in item["candidates"]
        for field in ("audio_path", "raw_audio_path")
        if services.database.candidates.get(candidate["id"])[field]
    )
    assert all(path.is_file() for path in paths)

    response = client.delete(f"/api/jobs/{created['id']}/items/{item['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/jobs/{created['id']}").status_code == 404
    assert all(not path.exists() for path in set(paths))
    assert services.database.candidates.get(item["candidates"][0]["id"]) is None
    assert not (services.settings.job_root / created["id"]).exists()


def test_delete_batch_job_item_preserves_other_lines(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "batch item deletion voice")
    script = _create_script(
        client,
        "batch-item.txt",
        "保留的台词 | mo-la\n要删除的台词 | gu-la\n",
    )
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    ).json()["job"]
    job = wait_for_job(client, created["id"])
    retained, deleted = job["items"]
    retained_audio = Path(
        services.database.jobs.item(created["id"], retained["id"])["audio_path"]
    )
    deleted_audio = Path(
        services.database.jobs.item(created["id"], deleted["id"])["audio_path"]
    )
    archive = client.get(job["download_url"])
    assert archive.status_code == 200
    archive_path = services.settings.export_root / f"{created['id']}.zip"
    assert archive_path.is_file()

    response = client.delete(f"/api/jobs/{created['id']}/items/{deleted['id']}")

    assert response.status_code == 204
    remaining = client.get(f"/api/jobs/{created['id']}")
    assert remaining.status_code == 200
    payload = remaining.json()["job"]
    assert payload["total_items"] == 1
    assert payload["completed_items"] == 1
    assert [item["id"] for item in payload["items"]] == [retained["id"]]
    assert client.get(payload["items"][0]["audio_url"]).status_code == 200
    assert retained_audio.is_file()
    assert not deleted_audio.exists()
    assert not archive_path.exists()
    assert services.database.jobs.item(created["id"], deleted["id"]) is None


def test_delete_running_job_item_is_rejected(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "running item deletion voice")
    script = _create_script(client, "running-item.txt", "稍后删除 | mo-la\n")
    created = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
        },
    ).json()["job"]
    item = services.database.jobs.items(created["id"])[0]
    services.database.jobs.set_running(created["id"])

    response = client.delete(f"/api/jobs/{created['id']}/items/{item['id']}")

    assert response.status_code == 409
    assert "处理完成" in response.json()["detail"]


def test_script_upload_does_not_require_voice(app_client) -> None:
    client, services = app_client
    before = services.database.monitoring.counts()["scripts"]

    response = client.post(
        "/api/scripts",
        files={"file": ("unbound.txt", "未绑定台词 | mo-la\n", "text/plain")},
    )

    assert response.status_code == 201
    script = response.json()["script"]
    assert "default_voice_id" not in script
    assert services.database.monitoring.counts()["scripts"] == before + 1

    response = client.post(
        "/api/scripts",
        files={"file": ("unusable.txt", "不可用台词 | mo-la\n", "text/plain")},
    )

    assert response.status_code == 201
    assert services.database.monitoring.counts()["scripts"] == before + 2

    response = client.post(
        "/api/scripts",
        files={"file": (f"{'x' * 81}.txt", "过长名称 | mo-la\n", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "台本名称需为 1-80 个字符"
    assert services.database.monitoring.counts()["scripts"] == before + 2


def test_job_requires_explicit_voice_selection(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "legacy script voice")
    uploaded = client.post(
        "/api/scripts",
        data={"default_voice_id": voice["id"]},
        files={"file": ("unbound.txt", "未绑定台词 | mo-la\n", "text/plain")},
    )
    script = uploaded.json()["script"]
    response = client.post(
        "/api/jobs",
        data={"model_id": "test_model", "script_id": script["id"]},
    )

    assert response.status_code == 422
    assert "请选择 1-4 个声音库" in response.json()["detail"]


def test_create_job_accepts_four_candidates(app_client) -> None:
    client, _ = app_client
    voice = _create_voice(client, "four candidate voice")
    script = _create_script(client, "four-candidates.txt", "four candidates | mo-la\n")

    response = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 4,
        },
    )

    assert response.status_code == 201
    job = wait_for_job(client, response.json()["job"]["id"])
    assert len(job["items"][0]["candidates"]) == 4


def test_raw_direction_override_keeps_ascii_punctuation() -> None:
    assert _directional_text("ABC", "rise", True) == "ABC?"
    assert _directional_text("ABC", "fall", True) == "ABC."
    assert _directional_text("ABC", "rise", False) == "ABC？"
    assert _directional_text("ABC", "fall", False) == "ABC。"


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


def test_script_line_selections_mark_collaboration_and_export_history(
    app_client,
) -> None:
    client, _ = app_client
    voice = _create_voice(client, "selection voice")
    script = _create_script(
        client,
        "selection.txt",
        "第一句 | mo-la\n第二句 | gu-na\n",
    )
    full_job = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 2,
        },
    ).json()["job"]
    single_job = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "line_number": 1,
            "candidate_count": 1,
        },
    ).json()["job"]
    full = wait_for_job(client, full_job["id"])
    single = wait_for_job(client, single_job["id"])

    incomplete = client.get(f"/api/scripts/{script['id']}/audio-export?scope=accepted")
    assert incomplete.status_code == 409

    first_line = single["items"][0]
    second_line = full["items"][1]
    selected_first = client.post(
        f"/api/scripts/{script['id']}/selections",
        json={
            "sequence": 1,
            "job_id": single["id"],
            "item_id": first_line["id"],
            "candidate_id": first_line["candidates"][0]["id"],
        },
    )
    assert selected_first.status_code == 200
    assert selected_first.json()["selection"]["selected_by_name"] == "系统管理员"
    selected_second = client.post(
        f"/api/scripts/{script['id']}/selections",
        json={
            "sequence": 2,
            "job_id": full["id"],
            "item_id": second_line["id"],
            "candidate_id": second_line["candidates"][1]["id"],
        },
    )
    assert selected_second.status_code == 200

    detail = client.get(f"/api/scripts/{script['id']}").json()["script"]
    assert [row["sequence"] for row in detail["selections"]] == [1, 2]
    assert detail["selections"][1]["candidate_id"] == second_line["candidates"][1]["id"]

    accepted = client.get(f"/api/scripts/{script['id']}/audio-export?scope=accepted")
    assert accepted.status_code == 200
    with zipfile.ZipFile(io.BytesIO(accepted.content)) as package:
        assert package.namelist() == ["Audio/001.wav", "Audio/002.wav", "manifest.json"]
        manifest = json.loads(package.read("manifest.json"))
    assert manifest["scope"] == "accepted"
    assert [item["candidateId"] for item in manifest["items"]] == [
        first_line["candidates"][0]["id"],
        second_line["candidates"][1]["id"],
    ]
    assert all(item["accepted"] for item in manifest["items"])

    history = client.get(f"/api/scripts/{script['id']}/audio-export?scope=all")
    assert history.status_code == 200
    with zipfile.ZipFile(io.BytesIO(history.content)) as package:
        names = package.namelist()
        history_manifest = json.loads(package.read("manifest.json"))
    assert names[-1] == "manifest.json"
    assert len(history_manifest["items"]) == 5
    assert sum(item["accepted"] for item in history_manifest["items"]) == 2

    cleared = client.delete(f"/api/scripts/{script['id']}/selections/1")
    assert cleared.status_code == 204
    assert client.get(f"/api/scripts/{script['id']}").json()["script"][
        "selections"
    ] == [detail["selections"][1]]
    edited = client.put(
        f"/api/scripts/{script['id']}/items",
        json={
            "items": [
                {"text": "改过的第一句", "pronunciation": "mo-la"},
                {"text": "第二句", "pronunciation": "gu-na"},
            ]
        },
    )
    assert edited.status_code == 200
    assert edited.json()["script"]["selections"] == []


def test_create_job_applies_custom_settings_and_reproducible_seed_sequence(
    app_client,
) -> None:
    client, services = app_client
    voice = _create_voice(client, "reproducible voice")
    script = _create_script(
        client,
        "reproducible.txt",
        "第一句 | mo-la\n第二句 | gu-na\n",
    )

    response = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 3,
            "base_seed": 0,
            "generation_settings": json.dumps(
                {"temperature": 0.63, "speed_factor": 0.91}
            ),
        },
    )
    assert response.status_code == 201
    job = wait_for_job(client, response.json()["job"]["id"])
    candidates = [
        candidate for item in job["items"] for candidate in item["candidates"]
    ]

    assert [candidate["seed"] for candidate in candidates] == list(range(6))
    assert services.engine.seed_calls[-6:] == list(range(6))
    assert all(
        candidate["generation_settings"]["temperature"] == 0.63
        for candidate in candidates
    )
    assert all(
        candidate["generation_settings"]["speed_factor"] == 0.91
        for candidate in candidates
    )
    assert all(
        call["temperature"] == 0.63
        for call in services.engine.generation_settings_calls[-6:]
    )


def test_create_job_can_generate_one_script_line(app_client) -> None:
    client, _ = app_client
    voice = _create_voice(client, "single line voice")
    script = _create_script(
        client,
        "single-line.txt",
        "第一句 | mo-la\n第二句 | gu-na\n",
    )

    whole_script = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 1,
        },
    )

    assert whole_script.status_code == 201
    whole_job = wait_for_job(client, whole_script.json()["job"]["id"])
    assert whole_job["total_items"] == 2
    assert all(len(item["candidates"]) == 1 for item in whole_job["items"])

    response = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 1,
            "line_number": 2,
        },
    )

    assert response.status_code == 201
    job = wait_for_job(client, response.json()["job"]["id"])
    assert job["total_items"] == 1
    assert job["items"][0]["sequence"] == 2
    assert len(job["items"][0]["candidates"]) == 1
    assert job["items"][0]["accepted_candidate_id"]

    invalid = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
            "script_id": script["id"],
            "candidate_count": 1,
            "line_number": 3,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "台本中没有第 3 行"


def test_create_jobs_compare_models_with_shared_seeds_and_model_defaults(
    settings_factory,
) -> None:
    settings = _multi_model_settings(settings_factory)
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    with TestClient(application) as client:
        user = login_as_admin(client, application.state.services)
        application.state.services.database.projects.create(
            str(user["id"]), "多模型测试项目", ""
        )
        voice = _create_voice(client, "comparison voice")
        script = _create_script(
            client,
            "comparison.txt",
            "第一句 | mo-la\n第二句 | gu-na\n",
        )
        response = client.post(
            "/api/jobs",
            data={
                "voice_id": voice["id"],
                "model_id": "primary_model",
                "model_ids": json.dumps(["secondary_model"]),
                "script_id": script["id"],
                "candidate_count": 2,
                "base_seed": 321,
                "generation_settings": json.dumps(
                    {"temperature": 0.61, "speed_factor": 0.91}
                ),
            },
        )

        assert response.status_code == 201
        created = response.json()
        assert created["job"] == created["jobs"][0]
        assert [job["model_id"] for job in created["jobs"]] == [
            "primary_model",
            "secondary_model",
        ]
        jobs = [wait_for_job(client, job["id"]) for job in created["jobs"]]

    candidate_groups = [
        [candidate for item in job["items"] for candidate in item["candidates"]]
        for job in jobs
    ]
    assert [
        [candidate["seed"] for candidate in group] for group in candidate_groups
    ] == [
        [321, 322, 323, 324],
        [321, 322, 323, 324],
    ]
    assert all(
        candidate["generation_settings"] == {"temperature": 0.61, "speed_factor": 0.91}
        for candidate in candidate_groups[0]
    )
    assert all(
        candidate["generation_settings"] == {"temperature": 0.95, "top_k": 42}
        for candidate in candidate_groups[1]
    )


def test_create_jobs_auto_shares_seed_and_rejects_unavailable_model(
    settings_factory,
) -> None:
    settings = _multi_model_settings(settings_factory)
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    with TestClient(application) as client:
        user = login_as_admin(client, application.state.services)
        application.state.services.database.projects.create(
            str(user["id"]), "多模型测试项目", ""
        )
        voice = _create_voice(client, "automatic seed voice")
        script = _create_script(client, "automatic-seed.txt", "台词 | mo-la\n")
        response = client.post(
            "/api/jobs",
            data={
                "voice_id": voice["id"],
                "model_id": "primary_model",
                "model_ids": json.dumps(["secondary_model"]),
                "script_id": script["id"],
            },
        )
        assert response.status_code == 201
        jobs = [wait_for_job(client, job["id"]) for job in response.json()["jobs"]]
        seeds = [
            [candidate["seed"] for candidate in job["items"][0]["candidates"]]
            for job in jobs
        ]
        assert seeds[0] == seeds[1]

        services = application.state.services
        services.engine.model_available = lambda model_id: SimpleNamespace(
            available=model_id != "secondary_model",
            reason="测试模型未安装" if model_id == "secondary_model" else "",
        )
        rejected = client.post(
            "/api/jobs",
            data={
                "voice_id": voice["id"],
                "model_id": "primary_model",
                "model_ids": json.dumps(["secondary_model"]),
                "script_id": script["id"],
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == "测试模型未安装"


def test_create_job_rejects_invalid_generation_settings_and_seed(app_client) -> None:
    client, _ = app_client
    voice = _create_voice(client, "invalid create settings voice")
    script = _create_script(client, "invalid-create.txt", "台词 | mo-la\n")
    base = {
        "voice_id": voice["id"],
        "model_id": "test_model",
        "script_id": script["id"],
    }

    invalid_settings = client.post(
        "/api/jobs",
        data={**base, "generation_settings": '{"top_k": 1.5}'},
    )
    invalid_seed = client.post(
        "/api/jobs",
        data={**base, "candidate_count": 3, "base_seed": 2_147_483_646},
    )

    assert invalid_settings.status_code == 422
    assert "必须是整数" in invalid_settings.json()["detail"]
    assert invalid_seed.status_code == 422
    assert "基准随机种子" in invalid_seed.json()["detail"]


def test_create_job_rejects_inline_script_upload(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "inline upload voice")
    before = services.database.monitoring.counts()["scripts"]

    response = client.post(
        "/api/jobs",
        data={
            "voice_id": voice["id"],
            "model_id": "test_model",
        },
        files={"script": ("inline.txt", "台词 | mo-la\n", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "请选择台本库中的台本"
    assert services.database.monitoring.counts()["scripts"] == before


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


def test_regenerate_inherits_unspecified_generation_settings(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "inherit settings voice")
    script = _create_script(client, "inherit-settings.txt", "台词 | mo-la\n")
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
    source = item["candidates"][0]

    response = client.post(
        f"/api/jobs/{job['id']}/items/{item['id']}/regenerate",
        json={
            "text": item["text"],
            "pronunciation": item["pronunciation"],
            "source_candidate_id": source["id"],
            "generation_settings": {"temperature": 1.05},
        },
    )
    assert response.status_code == 201
    candidate_id = response.json()["candidate"]["id"]
    candidate = wait_for_candidate(client, job["id"], item["id"], candidate_id)

    assert candidate["generation_settings"] == {
        **source["generation_settings"],
        "temperature": 1.05,
    }
    assert (
        services.engine.generation_settings_calls[-1]
        == candidate["generation_settings"]
    )


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


def test_audio_download_and_accept_fall_back_to_legacy_audio_path(app_client) -> None:
    client, services = app_client
    voice = _create_voice(client, "fallback voice")
    script = _create_script(client, "fallback.txt", "台词 | mo-la\n")
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
    candidate = item["candidates"][0]
    fallback = services.settings.job_root / job["id"] / "fallback.wav"
    fallback.write_bytes(make_wav_bytes(0.12))
    missing_raw = services.settings.job_root / job["id"] / "missing-raw.wav"

    with sqlite3.connect(services.settings.database_path) as connection:
        connection.execute(
            "UPDATE job_item_candidates SET raw_audio_path=?, audio_path=? WHERE id=?",
            (str(missing_raw), str(fallback), candidate["id"]),
        )
        connection.execute(
            "UPDATE job_items SET raw_audio_path=?, audio_path=? WHERE id=?",
            (str(missing_raw), str(fallback), item["id"]),
        )

    assert client.get(candidate["audio_url"]).content == fallback.read_bytes()
    assert client.get(job["items"][0]["audio_url"]).content == fallback.read_bytes()
    accepted = client.post(
        f"/api/jobs/{job['id']}/items/{item['id']}/accept",
        json={"candidate_id": candidate["id"]},
    )
    assert accepted.status_code == 200


def test_jobs_are_project_private_and_system_admin_can_access_all(
    settings_factory,
) -> None:
    settings = settings_factory()
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)

    with TestClient(application) as owner, TestClient(application) as stranger:
        services = application.state.services
        services.auth.create_user("owner", "项目负责人", "owner-password-123")
        services.auth.create_user("stranger", "其他成员", "stranger-password-123")
        owner_user = _login_user(owner, "owner", "owner-password-123")
        _login_user(stranger, "stranger", "stranger-password-123")
        owner_project = owner.post(
            "/api/projects", json={"name": "私有项目", "description": ""}
        ).json()["project"]
        stranger.post("/api/projects", json={"name": "其他项目", "description": ""})
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
        assert job["project_id"] == owner_project["id"]

        assert stranger.get("/api/admin/jobs").status_code == 403
        with TestClient(application) as admin:
            login_as_admin(admin, services)
            admin_jobs = admin.get("/api/admin/jobs").json()["jobs"]
            assert [row["id"] for row in admin_jobs] == [job["id"]]
            detail = admin.get(f"/api/admin/jobs/{job['id']}").json()["job"]
            assert detail["client_id"] == owner_user["id"]
            assert admin.get(detail["items"][0]["audio_url"]).status_code == 200


def test_docker_admin_token_bootstraps_login_password(
    settings_factory, monkeypatch
) -> None:
    monkeypatch.setenv("VOICE_LAB_ADMIN_TOKEN", "local-docker-token")
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application, client=("172.20.0.2", 50100)) as client:
        assert client.get("/admin").status_code == 200
        assert client.get("/api/admin/overview").status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            ).status_code
            == 401
        )
        user = _login_user(client, "admin", "local-docker-token")
        assert user["role"] == "system_admin"
        assert client.get("/api/admin/overview").status_code == 200


def test_voice_library_is_editable_by_project_members(
    settings_factory,
) -> None:
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )

    with TestClient(application) as owner, TestClient(application) as member:
        services = application.state.services
        services.auth.create_user("owner", "项目负责人", "owner-password-123")
        services.auth.create_user("member", "协作成员", "member-password-123")
        _login_user(owner, "owner", "owner-password-123")
        _login_user(member, "member", "member-password-123")
        project = owner.post(
            "/api/projects", json={"name": "协作项目", "description": ""}
        ).json()["project"]
        added = owner.post(
            f"/api/projects/{project['id']}/members", json={"username": "member"}
        )
        assert added.status_code == 201

        voice = _create_voice(owner, "source", 2)
        voice_id = voice["id"]
        member_detail = member.get(f"/api/voices/{voice_id}").json()["voice"]
        assert member_detail["can_edit"] is True
        file_id = member_detail["files"][0]["id"]
        disabled = member.patch(
            f"/api/voices/{voice_id}/files/{file_id}", json={"enabled": False}
        )
        assert disabled.status_code == 200

        updated = member.patch(
            f"/api/voices/{voice_id}",
            json={"name": "renamed source", "notes": "after"},
        )
        assert updated.status_code == 200
        appended = member.post(
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


def _login_user(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["user"]


def _create_script(client: TestClient, name: str, text: str) -> dict:
    voices = client.get("/api/voices").json()["voices"]
    assert voices
    response = client.post(
        "/api/scripts",
        data={"default_voice_id": voices[0]["id"]},
        files={"file": (name, text.encode(), "text/plain")},
    )
    assert response.status_code == 201
    return response.json()["script"]


def _multi_model_settings(settings_factory):
    settings = settings_factory()
    settings.profiles_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "primary_model",
                        "label": "Primary",
                        "generation_parameters": ["temperature", "speed_factor"],
                        "clone_overrides": {"temperature": 0.7, "speed_factor": 1.0},
                    },
                    {
                        "id": "secondary_model",
                        "label": "Secondary",
                        "generation_parameters": ["temperature", "top_k"],
                        "clone_overrides": {"temperature": 0.95, "top_k": 42},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return settings
