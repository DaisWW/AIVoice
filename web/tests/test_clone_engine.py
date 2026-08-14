from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from app.audio_conversion import SUPPORTED_AUDIO_EXTENSIONS
from conftest import make_encoded_audio_bytes
from voice_core.pronunciation import analyze_pronunciation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODE_ROOT = PROJECT_ROOT / "code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from gpt_sovits_clone import (  # noqa: E402
    AUDIO_EXTENSIONS,
    build_reference_audio,
    decode_audio,
    synthesize,
    to_float_audio,
    write_wav,
)


@pytest.mark.parametrize(
    ("filename", "container_format", "codec"),
    [
        ("phone.m4a", "ipod", "aac"),
        ("recorder.mp3", "mp3", "mp3"),
        ("voice.aac", "adts", "aac"),
        ("lossless.flac", "flac", "flac"),
        ("memo.ogg", "ogg", "libvorbis"),
        ("memo.opus", "opus", "libopus"),
    ],
)
def test_clone_reference_decoder_accepts_common_audio_formats(
    tmp_path: Path,
    filename: str,
    container_format: str,
    codec: str,
) -> None:
    path = tmp_path / filename
    path.write_bytes(make_encoded_audio_bytes(container_format, codec))

    sample_rate, audio = decode_audio(path)

    assert sample_rate in {16_000, 48_000}
    assert audio.ndim == 1
    assert audio.dtype == np.float32
    assert audio.size > 0


def test_clone_audio_extension_list_matches_web_uploads() -> None:
    assert AUDIO_EXTENSIONS == SUPPORTED_AUDIO_EXTENSIONS


@pytest.mark.parametrize(
    "audio",
    [
        np.array([[0.25, -0.25, 0.5]], dtype=np.float32),
        np.array([[0.25], [-0.25], [0.5]], dtype=np.float32),
        np.array([[0.2, -0.2, 0.4], [0.3, -0.3, 0.6]], dtype=np.float32),
        np.array([[0.2, 0.3], [-0.2, -0.3], [0.4, 0.6]], dtype=np.float32),
    ],
)
def test_float_audio_accepts_channel_first_and_sample_first(audio: np.ndarray) -> None:
    converted = to_float_audio(audio)

    assert converted.shape == (3,)
    assert np.allclose(converted, [0.25, -0.25, 0.5])


def test_unsigned_pcm_is_centered_around_zero() -> None:
    converted = to_float_audio(np.array([0, 128, 255], dtype=np.uint8))

    assert np.allclose(converted, [-1.0, 0.0, 127 / 128])


def test_synthesize_rejects_empty_model_output(tmp_path: Path) -> None:
    class EmptyTts:
        def run(self, inputs):
            del inputs
            yield 32_000, np.zeros(0, dtype=np.float32)

    with pytest.raises(RuntimeError, match="空音频"):
        synthesize(EmptyTts(), "摸啦。", tmp_path / "ref.wav", "", "", {}, 1)


def test_reference_builder_only_prepares_model_input(tmp_path: Path) -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
    tone = (0.2 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)
    sources = []
    for index in range(2):
        path = tmp_path / f"source-{index}.wav"
        write_wav(path, tone, sample_rate)
        sources.append(
            {
                "audio_path": path,
                "reference_text": "",
                "reference_lang": "all_zh",
            }
        )
    sources.append(
        {
            "audio_path": tmp_path / "unused-missing.wav",
            "reference_text": "",
            "reference_lang": "all_zh",
        }
    )

    target = tmp_path / "reference.wav"
    path, selected, duration, prompt, language = build_reference_audio(
        sources,
        target,
        sample_rate,
        target_seconds=3.5,
        max_seconds=6.0,
        gap_seconds=0.12,
        trim_settings={
            "threshold_db": -42,
            "padding_ms": 45,
            "minimum_clip_ms": 80,
        },
    )

    assert path == target
    assert path.is_file()
    assert len(selected) == 2
    assert 3.5 <= duration <= 6.0
    assert prompt == ""
    assert language == ""


def test_reference_builder_rejects_silent_recordings(tmp_path: Path) -> None:
    source = tmp_path / "silent.wav"
    write_wav(source, np.zeros(48_000 * 4, dtype=np.float32), 48_000)

    with pytest.raises(ValueError, match="没有可用于"):
        build_reference_audio(
            [{"audio_path": source, "reference_text": "", "reference_lang": "all_zh"}],
            tmp_path / "reference.wav",
            48_000,
            target_seconds=4.0,
            max_seconds=6.0,
            gap_seconds=0.12,
            trim_settings={
                "threshold_db": -42,
                "padding_ms": 45,
                "minimum_clip_ms": 80,
            },
        )


def test_pronunciation_holds_remain_metadata_for_external_editing() -> None:
    analysis = analyze_pronunciation("嗯——……GU-la。↘")

    assert analysis.generated_text == "嗯……咕啦。"
    assert analysis.direction == "fall"
    assert analysis.emphasis == ("gu",)
    assert analysis.hold_units == (2, 0, 0)
