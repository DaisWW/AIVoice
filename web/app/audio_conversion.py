from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

from .storage import validate_wav


SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
)
AUDIO_FORMAT_LABEL = "WAV、MP3、M4A、AAC、FLAC、OGG、OPUS"
TARGET_SAMPLE_RATE = 48_000
MAX_NORMALIZED_SECONDS = 120
MAX_NORMALIZED_SAMPLES = TARGET_SAMPLE_RATE * MAX_NORMALIZED_SECONDS


class AudioNormalizer:
    """Decode supported uploads into the WAV format required by inference."""

    def normalize(self, source: Path) -> Path:
        temporary = source.with_name(f"{source.stem}.normalized.wav")
        final = source.with_suffix(".wav")
        try:
            if self._convert(source, temporary) <= 0:
                raise ValueError(f"没有从音频中解码出有效内容: {source.name}")
            validate_wav(temporary)
            source.unlink(missing_ok=True)
            temporary.replace(final)
            return final
        except ValueError:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"音频无法解码: {source.name}") from error

    def _convert(self, source: Path, target: Path) -> int:
        try:
            import av
        except ImportError as error:  # pragma: no cover - deployment guard
            raise ValueError("服务器缺少音频解码组件 PyAV") from error

        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=TARGET_SAMPLE_RATE,
        )
        with av.open(str(source)) as container, wave.open(str(target), "wb") as output:
            self._configure_output(output)
            stream = self._audio_stream(container)
            samples = self._write_frames(output, resampler, container.decode(stream))
            return samples + self._write_frames(output, resampler, [None])

    @staticmethod
    def _configure_output(output: wave.Wave_write) -> None:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(TARGET_SAMPLE_RATE)

    @staticmethod
    def _audio_stream(container: Any) -> Any:
        stream = next(
            (stream for stream in container.streams if stream.type == "audio"),
            None,
        )
        if stream is None:
            raise ValueError("文件中没有音频轨道")
        return stream

    @staticmethod
    def _write_frames(output: wave.Wave_write, resampler: Any, frames: Any) -> int:
        samples = 0
        for frame in frames:
            for converted in resampler.resample(frame):
                frame_samples = int(converted.samples)
                if samples + frame_samples > MAX_NORMALIZED_SAMPLES:
                    raise ValueError(f"单条录音不能超过 {MAX_NORMALIZED_SECONDS} 秒，请拆成多条上传")
                output.writeframes(converted.to_ndarray().tobytes())
                samples += frame_samples
        return samples
