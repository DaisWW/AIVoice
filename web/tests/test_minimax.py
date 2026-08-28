from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import httpx
import pytest

from app.engines.contracts import ReferenceAudio
from app.engines.minimax import MAX_CLONE_SECONDS, MiniMaxAdapter
from app.provider_config import ProviderConfigStore

from conftest import make_wav_bytes


def _store(tmp_path: Path, *, enabled: bool = True) -> ProviderConfigStore:
    store = ProviderConfigStore(tmp_path / "provider-settings.json")
    store.update_provider(
        "minimax",
        {"enabled": enabled, "api_key": "test-api-key"},
    )
    return store


def _reference(
    first: Path,
    second: Path | None = None,
    *,
    prompt_text: str = "",
) -> ReferenceAudio:
    paths = (first, second) if second is not None else (first,)
    return ReferenceAudio(
        first,
        prompt_text,
        "",
        0.08,
        "voice-1",
        "虫语角色",
        paths,
    )


def _short_wav(path: Path, seconds: float, sample_rate: int = 1_000) -> None:
    path.write_bytes(make_wav_bytes(seconds, sample_rate))


def test_minimax_enrolls_once_and_wraps_raw_phonemes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    adapter = MiniMaxAdapter(store)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _short_wav(first, 6)
    _short_wav(second, 5)
    reference = _reference(first, second, prompt_text="ka.")
    calls = {"clone_upload": 0, "prompt_upload": 0, "clone": 0, "tts": 0}
    tts_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files/upload":
            if b"voice_clone" in request.content:
                calls["clone_upload"] += 1
                assert b"RIFF" in request.content
                return httpx.Response(
                    200,
                    json={
                        "file": {"file_id": 101},
                        "base_resp": {"status_code": 0},
                    },
                    request=request,
                )
            if b"prompt_audio" in request.content:
                calls["prompt_upload"] += 1
                return httpx.Response(
                    200,
                    json={
                        "file": {"file_id": 102},
                        "base_resp": {"status_code": 0},
                    },
                    request=request,
                )
            raise AssertionError("unexpected MiniMax upload purpose")
        if request.url.path == "/v1/voice_clone":
            calls["clone"] += 1
            payload = json.loads(request.content)
            assert payload["file_id"] == 101
            assert payload["voice_id"].startswith("VoiceLab_")
            assert payload["clone_prompt"] == {
                "prompt_audio": 102,
                "prompt_text": "ka.",
            }
            return httpx.Response(
                200,
                json={"base_resp": {"status_code": 0}},
                request=request,
            )
        if request.url.path == "/v1/t2a_v2":
            calls["tts"] += 1
            payload = json.loads(request.content)
            tts_bodies.append(payload)
            return httpx.Response(
                200,
                json={
                    "data": {"audio": make_wav_bytes(sample_rate=32_000).hex()},
                    "base_resp": {"status_code": 0},
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        MiniMaxAdapter,
        "_client",
        staticmethod(
            lambda config: httpx.Client(
                transport=transport,
                base_url=config["base_url"],
            )
        ),
    )

    for name in ("one.wav", "two.wav"):
        result = adapter.generate(
            {
                "generated_text": "t͡ʃa ʀ.",
                "pronunciation": "raw: t͡ʃa-ʀ",
            },
            reference,
            {},
            7,
            tmp_path / name,
            {},
        )
        assert result.audio_path.is_file()
        assert result.duration_seconds == 0.08

    assert calls == {
        "clone_upload": 1,
        "prompt_upload": 1,
        "clone": 1,
        "tts": 2,
    }
    assert tts_bodies[0]["text"] == "(t͡ʃa ʀ)."
    assert tts_bodies[0]["audio_setting"] == {
        "sample_rate": 32_000,
        "format": "wav",
        "channel": 1,
    }
    fingerprint = adapter._enrollment_fingerprint(
        reference.source_paths,
        store.provider("minimax"),
        reference.prompt_text,
        reference.path,
    )
    assert (
        store.enrollment("minimax", "voice-1", fingerprint)
        == tts_bodies[0]["voice_setting"]["voice_id"]
    )
    entry = json.loads(store.path.read_text(encoding="utf-8"))["enrollments"][
        "minimax"
    ]["voice-1"]
    assert entry["last_used_at"]
    assert fingerprint != adapter._enrollment_fingerprint(
        reference.source_paths,
        store.provider("minimax"),
        "different prompt",
        reference.path,
    )


def test_minimax_rejects_less_than_ten_seconds_before_upload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    adapter = MiniMaxAdapter(store)
    sample = tmp_path / "short.wav"
    _short_wav(sample, 9.9)

    with pytest.raises(RuntimeError, match="至少 10 秒"):
        adapter.generate(
            {"generated_text": "ka."},
            _reference(sample),
            {},
            1,
            tmp_path / "result.wav",
            {},
        )


def test_minimax_combined_audio_respects_five_minute_limit(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _short_wav(first, 220)
    _short_wav(second, 220)

    payload, source_seconds = MiniMaxAdapter._combined_clone_wav((first, second))

    with wave.open(io.BytesIO(payload), "rb") as audio:
        combined_seconds = audio.getnframes() / audio.getframerate()
    assert source_seconds <= MAX_CLONE_SECONDS
    assert combined_seconds <= MAX_CLONE_SECONDS
    assert source_seconds > 299


def test_minimax_combined_audio_skips_empty_sources(tmp_path: Path) -> None:
    empty = tmp_path / "empty.wav"
    valid = tmp_path / "valid.wav"
    _short_wav(empty, 0)
    _short_wav(valid, 10)

    payload, source_seconds = MiniMaxAdapter._combined_clone_wav((empty, valid))

    with wave.open(io.BytesIO(payload), "rb") as audio:
        assert audio.getnframes() == 10 * audio.getframerate()
    assert source_seconds == pytest.approx(10)


def test_minimax_reports_provider_status_errors(tmp_path: Path) -> None:
    store = _store(tmp_path)
    adapter = MiniMaxAdapter(store)
    response = httpx.Response(
        200,
        json={
            "base_resp": {
                "status_code": 1004,
                "status_msg": "invalid api key",
            }
        },
    )

    with pytest.raises(RuntimeError, match="状态码 1004") as error:
        adapter._success_payload(response, "连接检测")
    assert "invalid api key" not in str(error.value)


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
def test_minimax_rejects_invalid_url_or_numeric_config(config) -> None:
    with pytest.raises(RuntimeError, match="(地址无效|配置无效)"):
        MiniMaxAdapter._request(config, "连接检测", "GET", "/v1/models")


@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    (
        (404, {}, False),
        (
            400,
            {"base_resp": {"status_code": 1004, "status_msg": "invalid voice setting"}},
            False,
        ),
        (
            404,
            {"base_resp": {"status_code": 1004, "status_msg": "voice not found"}},
            True,
        ),
        (
            400,
            {"base_resp": {"status_code": 1004, "status_msg": "voice expired"}},
            True,
        ),
    ),
)
def test_minimax_reclone_requires_explicit_voice_error(
    status_code, payload, expected
) -> None:
    response = httpx.Response(status_code, json=payload)

    assert MiniMaxAdapter._is_missing_voice(response) is expected
