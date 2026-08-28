from __future__ import annotations

import json
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
    entry = json.loads(store.path.read_text(encoding="utf-8"))["enrollments"][
        "elevenlabs"
    ]["voice-1"]
    assert entry["last_used_at"]


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


def test_elevenlabs_closes_streamed_sample_handles(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    adapter = ElevenLabsAdapter(store)
    reference = _reference(tmp_path)
    captured = []

    def request(config, action, method, path, **options):
        del config, action, method, path
        captured.extend(file[1][1] for file in options["files"])
        assert all(not handle.closed for handle in captured)
        return httpx.Response(200, json={"voice_id": "remote-voice-1"})

    monkeypatch.setattr(adapter, "_request", request)

    adapter._ensure_voice(reference, store.provider("elevenlabs"))

    assert captured
    assert all(handle.closed for handle in captured)


def test_elevenlabs_rejects_empty_text_before_voice_enrollment(
    tmp_path, monkeypatch
) -> None:
    adapter = ElevenLabsAdapter(_store(tmp_path))
    monkeypatch.setattr(
        adapter,
        "_ensure_voice",
        lambda *_args, **_kwargs: pytest.fail("empty text must not enroll a voice"),
    )

    with pytest.raises(RuntimeError, match="生成文本不能为空"):
        adapter.generate(
            {"generated_text": ""},
            _reference(tmp_path),
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


@pytest.mark.parametrize(
    "config",
    (
        {
            "base_url": "https://example.com:bad",
            "api_key": "key",
            "request_timeout_seconds": 30,
        },
        {
            "base_url": "https://example.com",
            "api_key": "key",
            "request_timeout_seconds": {},
        },
    ),
)
def test_elevenlabs_rejects_invalid_url_or_numeric_config(config) -> None:
    with pytest.raises(RuntimeError, match="(地址无效|配置无效)"):
        ElevenLabsAdapter._request(config, "连接检测", "GET", "/v1/models")


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


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        ({"detail": {"status": "voice_not_found"}}, True),
        ({"detail": {"status": "voice_expired"}}, True),
        (
            {"detail": {"status": "invalid_api_key", "message": "invalid API key"}},
            False,
        ),
    ),
)
def test_elevenlabs_reclone_requires_explicit_voice_error(payload, expected) -> None:
    response = httpx.Response(404, json=payload)

    assert ElevenLabsAdapter._is_missing_voice(response) is expected
