from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.engines.contracts import ReferenceAudio
from app.engines.elevenlabs import ElevenLabsAdapter
from app.provider_config import ProviderConfigStore

from conftest import make_wav_bytes


def _store(tmp_path: Path, *, enabled: bool = True) -> ProviderConfigStore:
    store = ProviderConfigStore(tmp_path / "provider-settings.json")
    store.update_provider(
        "elevenlabs",
        {"enabled": enabled, "api_key": "test-api-key"},
    )
    return store


def _reference(tmp_path: Path) -> ReferenceAudio:
    path = tmp_path / "reference.wav"
    path.write_bytes(make_wav_bytes(sample_rate=48_000))
    return ReferenceAudio(
        path,
        "",
        "",
        0.08,
        "voice-1",
        "虫语角色",
        (path,),
    )


def test_elevenlabs_enrolls_once_and_reuses_cached_voice(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    adapter = ElevenLabsAdapter(store)
    calls = {"enroll": 0, "tts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/voices/add":
            calls["enroll"] += 1
            return httpx.Response(
                200, json={"voice_id": "remote-voice-1"}, request=request
            )
        if request.url.path == "/v1/text-to-speech/remote-voice-1":
            calls["tts"] += 1
            return httpx.Response(
                200,
                content=make_wav_bytes(sample_rate=48_000),
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        ElevenLabsAdapter,
        "_client",
        staticmethod(
            lambda config: httpx.Client(
                transport=transport, base_url=config["base_url"]
            )
        ),
    )
    reference = _reference(tmp_path)
    for name in ("one.wav", "two.wav"):
        result = adapter.generate(
            {"generated_text": "ka-lo"},
            reference,
            {},
            7,
            tmp_path / name,
            {},
        )
        assert result.audio_path.is_file()
        assert result.duration_seconds == 0.08

    assert calls == {"enroll": 1, "tts": 2}
    fingerprint = adapter._enrollment_fingerprint(
        reference.source_paths, store.provider("elevenlabs")
    )
    assert store.enrollment("elevenlabs", "voice-1", fingerprint) == "remote-voice-1"


def test_elevenlabs_uploads_all_enabled_source_samples(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    adapter = ElevenLabsAdapter(store)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(make_wav_bytes(sample_rate=48_000))
    second.write_bytes(make_wav_bytes(duration_seconds=0.12, sample_rate=48_000))
    reference = ReferenceAudio(
        first,
        "",
        "",
        0.08,
        "voice-1",
        "虫语角色",
        (first, second),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/voices/add":
            assert b'filename="first.wav"' in request.content
            assert b'filename="second.wav"' in request.content
            return httpx.Response(
                200, json={"voice_id": "remote-voice-1"}, request=request
            )
        return httpx.Response(
            200,
            content=make_wav_bytes(sample_rate=48_000),
            request=request,
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        ElevenLabsAdapter,
        "_client",
        staticmethod(
            lambda config: httpx.Client(
                transport=transport, base_url=config["base_url"]
            )
        ),
    )

    adapter.generate(
        {"generated_text": "ka-lo"},
        reference,
        {},
        7,
        tmp_path / "result.wav",
        {},
    )


def test_elevenlabs_connection_can_be_checked_before_enable(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path, enabled=False)
    adapter = ElevenLabsAdapter(store)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[], request=request)
    )
    monkeypatch.setattr(
        ElevenLabsAdapter,
        "_client",
        staticmethod(
            lambda config: httpx.Client(
                transport=transport, base_url=config["base_url"]
            )
        ),
    )

    assert adapter.status({}).available is False
    assert adapter.test_connection()["ok"] is True


def test_elevenlabs_network_errors_are_sanitized(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    adapter = ElevenLabsAdapter(store)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-api-key-must-not-escape", request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        ElevenLabsAdapter,
        "_client",
        staticmethod(
            lambda config: httpx.Client(
                transport=transport, base_url=config["base_url"]
            )
        ),
    )

    with pytest.raises(RuntimeError, match="网络请求失败") as error:
        adapter.test_connection()
    assert "secret-api-key-must-not-escape" not in str(error.value)


def test_elevenlabs_is_unavailable_without_key(tmp_path) -> None:
    store = ProviderConfigStore(tmp_path / "provider-settings.json")
    store.update_provider("elevenlabs", {"enabled": True, "api_key": ""})
    adapter = ElevenLabsAdapter(store)

    assert adapter.status({}).available is False
    with pytest.raises(RuntimeError, match="API Key"):
        adapter.generate(
            {"generated_text": "ka"},
            _reference(tmp_path),
            {},
            1,
            tmp_path / "out.wav",
            {},
        )
