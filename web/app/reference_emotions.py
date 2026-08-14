from __future__ import annotations

from typing import Any


REFERENCE_EMOTIONS = (
    {"id": "neutral", "label": "自然", "description": "普通说话与通用参考"},
    {"id": "calm", "label": "平静", "description": "克制、平稳、低起伏"},
    {"id": "angry", "label": "愤怒", "description": "强力度与明显爆发"},
    {"id": "whisper", "label": "耳语", "description": "轻声、气声与近距离"},
    {"id": "aged", "label": "衰老", "description": "年长、虚弱或粗粝演绎"},
    {"id": "sigh", "label": "叹息", "description": "叹气、疲惫与情绪尾音"},
)

EMOTION_IDS = {item["id"] for item in REFERENCE_EMOTIONS}


def validate_emotion(value: str, *, allow_all: bool = False) -> str:
    resolved = value.strip().lower() or ("all" if allow_all else "neutral")
    allowed = EMOTION_IDS | ({"all"} if allow_all else set())
    if resolved not in allowed:
        raise ValueError(f"未知参考语气: {value}")
    return resolved


def public_reference_emotions() -> list[dict[str, Any]]:
    return [dict(item) for item in REFERENCE_EMOTIONS]
