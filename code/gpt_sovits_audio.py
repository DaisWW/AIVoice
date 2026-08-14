from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import av
import numpy as np
from scipy import signal
from scipy.io import wavfile

try:
    import soxr
except ImportError:  # pragma: no cover - scipy remains a valid fallback
    soxr = None


AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"})


def decode_audio(path: Path) -> tuple[int, np.ndarray]:
    """Decode a reference recording to mono float audio."""
    if path.suffix.lower() == ".wav":
        try:
            sample_rate, audio = wavfile.read(path)
            return int(sample_rate), to_float_audio(audio)
        except Exception:
            pass

    frames: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = next(
            (item for item in container.streams if item.type == "audio"), None
        )
        if stream is None:
            raise ValueError(f"文件中没有音频轨道: {path.name}")
        sample_rate = int(stream.rate or 48_000)
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
        for frame in container.decode(stream.index):
            frames.extend(_resampled_frames(resampler, frame))
        frames.extend(_resampled_frames(resampler, None))
    if not frames:
        raise ValueError(f"没有解码到音频帧: {path.name}")
    return sample_rate, np.concatenate(frames).astype(np.float32)


def _resampled_frames(resampler: Any, frame: Any) -> list[np.ndarray]:
    return [
        np.asarray(converted.to_ndarray(), dtype=np.float32).reshape(-1)
        for converted in resampler.resample(frame)
    ]


def to_float_audio(audio: np.ndarray) -> np.ndarray:
    if np.issubdtype(audio.dtype, np.unsignedinteger):
        info = np.iinfo(audio.dtype)
        midpoint = (float(info.max) + 1.0) / 2.0
        audio = (audio.astype(np.float32) - midpoint) / midpoint
    elif np.issubdtype(audio.dtype, np.signedinteger):
        info = np.iinfo(audio.dtype)
        scale = float(max(abs(info.min), info.max))
        audio = audio.astype(np.float32) / scale
    else:
        audio = audio.astype(np.float32)
    audio = np.squeeze(audio)
    if audio.ndim == 2:
        channel_axis = 0 if audio.shape[0] <= audio.shape[1] else 1
        audio = audio.mean(axis=channel_axis)
    return np.nan_to_num(audio.reshape(-1), nan=0.0, posinf=0.0, neginf=0.0)


def resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32)
    if soxr is not None:
        return np.asarray(
            soxr.resample(audio, source_rate, target_rate, quality="HQ"),
            dtype=np.float32,
        )
    divisor = math.gcd(source_rate, target_rate)
    return signal.resample_poly(
        audio,
        target_rate // divisor,
        source_rate // divisor,
    ).astype(np.float32)


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.int16(np.clip(audio, -1.0, 1.0) * 32767))


def _prepare_reference_clip(
    path: Path,
    target_rate: int,
    trim_settings: dict[str, Any],
) -> np.ndarray:
    source_rate, audio = decode_audio(path)
    audio = resample_audio(audio, source_rate, target_rate)
    if audio.size:
        audio = audio - float(np.mean(audio))
        peak = float(np.max(np.abs(audio)))
        if peak > 0.95:
            audio *= 0.95 / peak
    return _trim_silence(
        audio.astype(np.float32),
        target_rate,
        float(trim_settings["threshold_db"]),
        int(trim_settings["padding_ms"]),
        int(trim_settings["minimum_clip_ms"]),
    )


def _trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    threshold_db: float,
    padding_ms: int,
    minimum_ms: int,
) -> np.ndarray:
    if audio.size == 0:
        return audio
    first_count = min(audio.size, int(sample_rate * 0.12))
    noise_rms = float(np.sqrt(np.mean(np.square(audio[:first_count])) + 1e-12))
    threshold = max(10 ** (threshold_db / 20), noise_rms * 3.2)
    active = np.flatnonzero(np.abs(audio) >= threshold)
    if active.size == 0:
        minimum_peak = 10 ** (threshold_db / 20)
        return audio if float(np.max(np.abs(audio))) >= minimum_peak else audio[:0]
    padding = int(sample_rate * padding_ms / 1000)
    start = max(0, int(active[0]) - padding)
    end = min(audio.size, int(active[-1]) + padding + 1)
    clipped = audio[start:end]
    minimum_samples = int(sample_rate * minimum_ms / 1000)
    if clipped.size < minimum_samples:
        center = (start + end) // 2
        start = max(0, center - minimum_samples // 2)
        end = min(audio.size, start + minimum_samples)
        clipped = audio[start:end]
    return clipped.astype(np.float32)


def build_reference_audio(
    records: list[dict[str, Any]],
    output_path: Path,
    sample_rate: int,
    target_seconds: float,
    max_seconds: float,
    gap_seconds: float,
    trim_settings: dict[str, Any],
) -> tuple[Path, list[dict[str, Any]], float, str, str]:
    selected: list[dict[str, Any]] = []
    parts: list[np.ndarray] = []
    current_samples = 0
    maximum_samples = int(max_seconds * sample_rate)
    gap = np.zeros(int(gap_seconds * sample_rate), dtype=np.float32)
    for record in records:
        clip = _prepare_reference_clip(
            Path(record["audio_path"]), sample_rate, trim_settings
        )
        if clip.size == 0:
            continue
        remaining = maximum_samples - current_samples - (gap.size if parts else 0)
        if remaining <= 0:
            break
        clip = clip[:remaining]
        if parts:
            parts.append(gap)
            current_samples += gap.size
        parts.append(clip)
        selected.append(record)
        current_samples += clip.size
        if current_samples >= int(target_seconds * sample_rate):
            break
    if not parts:
        raise ValueError("没有可用于构建参考音频的片段")

    audio = np.concatenate(parts).astype(np.float32)
    duration = audio.size / sample_rate
    if duration < 3.0 or duration > 10.0:
        raise ValueError(f"GPT-SoVITS 参考音频必须为 3~10 秒，当前 {duration:.3f} 秒")
    fade = min(int(sample_rate * 0.012), audio.size // 4)
    if fade:
        curve = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[:fade] *= curve
        audio[-fade:] *= curve[::-1]
    write_wav(output_path, audio, sample_rate)
    prompt_text, prompt_lang = _reference_prompt(selected)
    return output_path, selected, duration, prompt_text, prompt_lang


def _reference_prompt(records: list[dict[str, Any]]) -> tuple[str, str]:
    if not records or any(
        not str(record.get("reference_text") or "") for record in records
    ):
        return "", ""
    languages = {str(record.get("reference_lang") or "all_zh") for record in records}
    if len(languages) != 1:
        raise ValueError("同一条拼接参考音的 reference_lang 必须一致")
    parts: list[str] = []
    for record in records:
        text = str(record["reference_text"]).strip()
        if text[-1] not in "。？！……,.!?":
            text += "。"
        parts.append(text)
    return "".join(parts), languages.pop()
