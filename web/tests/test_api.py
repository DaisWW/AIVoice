from __future__ import annotations

import io
import json
import sqlite3
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

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
    assert response.json()["user"]["role"] == "system_admin"


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
