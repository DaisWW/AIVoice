from __future__ import annotations

import math
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


class AudioQualityAnalyzer:
    """Fast, deterministic diagnostics for normalized voice-reference WAV files."""

    def analyze(self, path: Path) -> dict[str, Any]:
        sample_rate, audio = self._read(path)
        duration = audio.size / sample_rate if sample_rate else 0.0
        frame_rms = self._frame_rms(audio, max(1, int(sample_rate * 0.02)))
        frame_db = self._db(frame_rms)
        rms_db = self._scalar_db(self._rms(audio))
        noise_floor_db = float(np.percentile(frame_db, 20)) if frame_db.size else -120.0
        speech_db = float(np.percentile(frame_db, 80)) if frame_db.size else -120.0
        snr_db = max(0.0, speech_db - noise_floor_db)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        peak_db = self._scalar_db(peak)
        clipped_ratio = float(np.mean(np.abs(audio) >= 0.995)) if audio.size else 0.0
        silence_ratio = (
            float(np.mean(frame_db < max(-52.0, noise_floor_db + 3.0)))
            if frame_db.size
            else 1.0
        )
        reverb_score = self._reverb_score(frame_rms)
        issues = self._issues(
            duration,
            rms_db,
            snr_db,
            clipped_ratio,
            silence_ratio,
            reverb_score,
        )
        score = max(0, 100 - sum(int(issue["penalty"]) for issue in issues))
        return {
            "score": score,
            "grade": "good" if score >= 80 else "warn" if score >= 55 else "poor",
            "duration_seconds": round(duration, 2),
            "peak_dbfs": round(peak_db, 1),
            "rms_dbfs": round(rms_db, 1),
            "noise_floor_dbfs": round(noise_floor_db, 1),
            "snr_db": round(snr_db, 1),
            "silence_ratio": round(silence_ratio, 3),
            "clipped_ratio": round(clipped_ratio, 5),
            "reverb_risk": (
                "high"
                if reverb_score >= 0.72
                else "medium"
                if reverb_score >= 0.5
                else "low"
            ),
            "reverb_score": round(reverb_score, 3),
            "issues": [
                {key: value for key, value in issue.items() if key != "penalty"}
                for issue in issues
            ],
            "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    @staticmethod
    def _read(path: Path) -> tuple[int, np.ndarray]:
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2:
                raise ValueError("质量检测仅支持 16-bit PCM WAV")
            sample_rate = source.getframerate()
            channels = source.getnchannels()
            payload = source.readframes(source.getnframes())
        audio = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        return sample_rate, audio

    @staticmethod
    def _frame_rms(audio: np.ndarray, frame_size: int) -> np.ndarray:
        if not audio.size:
            return np.zeros(0, dtype=np.float32)
        padded = np.pad(audio, (0, (-audio.size) % frame_size))
        frames = padded.reshape(-1, frame_size)
        return np.sqrt(np.mean(np.square(frames), axis=1) + 1e-12)

    @staticmethod
    def _rms(audio: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + 1e-12))

    @staticmethod
    def _scalar_db(value: float) -> float:
        return 20.0 * math.log10(max(value, 1e-6))

    @staticmethod
    def _db(values: np.ndarray) -> np.ndarray:
        return 20.0 * np.log10(np.maximum(values, 1e-6))

    @staticmethod
    def _reverb_score(frame_rms: np.ndarray) -> float:
        if frame_rms.size < 20 or float(np.max(frame_rms)) < 1e-5:
            return 0.0
        envelope = frame_rms - float(np.mean(frame_rms))
        energy = float(np.dot(envelope, envelope))
        if energy <= 1e-10:
            return 0.0
        correlations = []
        for lag in range(3, min(26, envelope.size // 2)):
            left = envelope[:-lag]
            right = envelope[lag:]
            denominator = math.sqrt(
                float(np.dot(left, left)) * float(np.dot(right, right))
            )
            if denominator > 1e-10:
                correlations.append(max(0.0, float(np.dot(left, right)) / denominator))
        return max(correlations, default=0.0)

    @staticmethod
    def _issues(
        duration: float,
        rms_db: float,
        snr_db: float,
        clipped_ratio: float,
        silence_ratio: float,
        reverb_score: float,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if duration < 2.5:
            issues.append(
                AudioQualityAnalyzer._issue("duration", "录音偏短", "建议单条 3-15 秒", 20)
            )
        elif duration > 20:
            issues.append(
                AudioQualityAnalyzer._issue("duration", "录音偏长", "建议拆成多条 3-15 秒录音", 8)
            )
        if clipped_ratio > 0.001:
            issues.append(
                AudioQualityAnalyzer._issue("clipping", "存在削波", "降低录音增益并远离麦克风一点", 28)
            )
        if rms_db < -35:
            issues.append(
                AudioQualityAnalyzer._issue("loudness", "录音过轻", "靠近麦克风或提高输入增益", 15)
            )
        elif rms_db > -10:
            issues.append(
                AudioQualityAnalyzer._issue("loudness", "录音过响", "降低输入增益以保留动态", 10)
            )
        if snr_db < 12:
            issues.append(
                AudioQualityAnalyzer._issue("noise", "底噪明显", "关闭风扇与空调，靠近麦克风重录", 24)
            )
        elif snr_db < 20:
            issues.append(
                AudioQualityAnalyzer._issue("noise", "底噪略高", "换更安静的环境会更稳定", 10)
            )
        if silence_ratio > 0.55:
            issues.append(
                AudioQualityAnalyzer._issue("silence", "静音过多", "裁掉过长的句首句尾空白", 12)
            )
        if reverb_score >= 0.72:
            issues.append(
                AudioQualityAnalyzer._issue("reverb", "混响风险较高", "远离墙角并使用吸音较好的房间", 20)
            )
        elif reverb_score >= 0.5:
            issues.append(
                AudioQualityAnalyzer._issue("reverb", "可能有房间反射", "缩短麦克风距离可改善", 8)
            )
        return issues

    @staticmethod
    def _issue(code: str, label: str, message: str, penalty: int) -> dict[str, Any]:
        return {"code": code, "label": label, "message": message, "penalty": penalty}
