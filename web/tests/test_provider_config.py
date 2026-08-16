from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from conftest import FakeEngine


ADMIN_PASSWORD = "admin-password-123"


class ProviderTestEngine(FakeEngine):
    def test_provider(self, provider_id: str) -> dict[str, object]:
        assert provider_id == "elevenlabs"
        return {
            "ok": True,
            "message": "ElevenLabs API 连接正常",
            "model_count": 3,
            "elapsed_seconds": 0.01,
        }


def _admin_client(
    settings, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, object]:
    monkeypatch.setenv("VOICE_LAB_ADMIN_PASSWORD", ADMIN_PASSWORD)
    application = create_app(settings, engine_factory=FakeEngine, seed_legacy=False)
    client = TestClient(application)
    client.__enter__()
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return client, application


def _provider_payload(**overrides):
    payload = {
        "enabled": False,
        "api_key": "secret-key",
        "clear_api_key": False,
        "base_url": "https://api.elevenlabs.io",
        "tts_model_id": "eleven_multilingual_v2",
        "output_format": "wav_48000",
        "request_timeout_seconds": 180,
        "remove_background_noise": False,
        "stability": 0.5,
        "similarity_boost": 0.85,
        "style": 0.0,
        "use_speaker_boost": True,
    }
    payload.update(overrides)
    return payload


def _minimax_payload(**overrides):
    payload = {
        "enabled": False,
        "api_key": "minimax-secret-key",
        "clear_api_key": False,
        "base_url": "https://api.minimaxi.com",
        "tts_model_id": "speech-2.8-hd",
        "output_format": "wav",
        "sample_rate": 44100,
        "request_timeout_seconds": 180,
        "language_boost": "auto",
        "speed": 1.0,
        "volume": 1.0,
        "pitch": 0,
        "emotion": "",
        "need_noise_reduction": False,
        "need_volume_normalization": False,
    }
    payload.update(overrides)
    return payload


def test_provider_public_payload_masks_secret_and_blank_update_preserves_key(
    settings_factory, monkeypatch
) -> None:
    client, application = _admin_client(settings_factory(), monkeypatch)
    try:
        saved = client.patch(
            "/api/admin/providers/elevenlabs",
            json=_provider_payload(),
        )
        assert saved.status_code == 200
        public = saved.json()["providers"]["elevenlabs"]
        assert "api_key" not in public
        assert public["api_key_configured"] is True

        preserved = client.patch(
            "/api/admin/providers/elevenlabs",
            json=_provider_payload(api_key=None, tts_model_id="eleven_turbo_v2_5"),
        )
        assert preserved.status_code == 200
        assert (
            application.state.services.provider_config.provider("elevenlabs")["api_key"]
            == "secret-key"
        )

        cleared = client.patch(
            "/api/admin/providers/elevenlabs",
            json=_provider_payload(api_key=None, clear_api_key=True),
        )
        assert cleared.status_code == 200
        assert cleared.json()["providers"]["elevenlabs"]["api_key_configured"] is False
        raw = json.loads(
            application.state.services.provider_config.path.read_text(encoding="utf-8")
        )
        assert raw["providers"]["elevenlabs"]["api_key"] == ""
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    "changes",
    (
        {"base_url": "http://remote.example", "tts_model_id": "bad model"},
        {"base_url": "https://api.example/path", "tts_model_id": "valid_model"},
        {"base_url": "https://api.example:bad", "tts_model_id": "valid_model"},
    ),
)
def test_provider_update_rejects_unsafe_endpoint_or_model(
    settings_factory, monkeypatch, changes
) -> None:
    client, _ = _admin_client(settings_factory(), monkeypatch)
    try:
        response = client.patch(
            "/api/admin/providers/elevenlabs",
            json=_provider_payload(**changes),
        )
        assert response.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_provider_cannot_be_enabled_while_clearing_key(
    settings_factory, monkeypatch
) -> None:
    client, application = _admin_client(settings_factory(), monkeypatch)
    try:
        response = client.patch(
            "/api/admin/providers/elevenlabs",
            json=_provider_payload(enabled=True, clear_api_key=True),
        )
        assert response.status_code == 422
        stored = application.state.services.provider_config.provider("elevenlabs")
        assert stored["enabled"] is False
        assert stored["api_key"] == ""
    finally:
        client.__exit__(None, None, None)


def test_minimax_provider_masks_key_and_rejects_unsupported_audio_settings(
    settings_factory, monkeypatch
) -> None:
    client, application = _admin_client(settings_factory(), monkeypatch)
    try:
        saved = client.patch(
            "/api/admin/providers/minimax",
            json=_minimax_payload(),
        )
        assert saved.status_code == 200
        public = saved.json()["providers"]["minimax"]
        assert "api_key" not in public
        assert public["api_key_configured"] is True
        assert public["sample_rate"] == 44100
        assert (
            application.state.services.provider_config.provider("minimax")["api_key"]
            == "minimax-secret-key"
        )

        assert (
            client.patch(
                "/api/admin/providers/minimax",
                json=_minimax_payload(sample_rate=48000),
            ).status_code
            == 422
        )
        assert (
            client.patch(
                "/api/admin/providers/minimax",
                json=_minimax_payload(volume=0),
            ).status_code
            == 422
        )
    finally:
        client.__exit__(None, None, None)


def test_provider_routes_require_system_admin(settings_factory, monkeypatch) -> None:
    monkeypatch.setenv("VOICE_LAB_ADMIN_PASSWORD", ADMIN_PASSWORD)
    application = create_app(
        settings_factory(), engine_factory=FakeEngine, seed_legacy=False
    )
    with TestClient(application) as client:
        assert client.get("/api/admin/providers").status_code == 401
        member_password = "member-password-123"
        application.state.services.auth.create_user("member", "普通用户", member_password)
        login = client.post(
            "/api/auth/login",
            json={"username": "member", "password": member_password},
        )
        assert login.status_code == 200
        assert client.get("/api/admin/providers").status_code == 403
        assert (
            client.patch(
                "/api/admin/providers/elevenlabs", json=_provider_payload()
            ).status_code
            == 403
        )
        assert client.post("/api/admin/providers/elevenlabs/test").status_code == 403


def test_admin_can_test_saved_provider_connection(
    settings_factory, monkeypatch
) -> None:
    monkeypatch.setenv("VOICE_LAB_ADMIN_PASSWORD", ADMIN_PASSWORD)
    application = create_app(
        settings_factory(), engine_factory=ProviderTestEngine, seed_legacy=False
    )
    with TestClient(application) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200
        response = client.post("/api/admin/providers/elevenlabs/test")
        assert response.status_code == 200
        assert response.json()["model_count"] == 3


def test_reference_text_is_persisted_in_voice_payload(
    settings_factory, monkeypatch
) -> None:
    client, _ = _admin_client(settings_factory(), monkeypatch)
    try:
        from conftest import make_wav_bytes

        created = client.post(
            "/api/voices",
            data={"name": "虫语样本"},
            files={"files": ("sample.wav", make_wav_bytes(), "audio/wav")},
        )
        assert created.status_code == 201
        voice_id = created.json()["voice"]["id"]
        detail = client.get(f"/api/voices/{voice_id}").json()["voice"]
        file_id = detail["files"][0]["id"]

        updated = client.patch(
            f"/api/voices/{voice_id}/files/{file_id}",
            json={"reference_text": "  ka-lo  na  "},
        )
        assert updated.status_code == 200
        assert updated.json()["voice"]["files"][0]["reference_text"] == "ka-lo  na"
    finally:
        client.__exit__(None, None, None)
