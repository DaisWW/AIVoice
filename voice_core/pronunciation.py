from __future__ import annotations

import re
from dataclasses import dataclass


HOLD_DASHES = "—–―－"
MAX_HOLD_UNITS = 6
TOKEN_RE = re.compile(r"[A-Za-z]+|[^A-Za-z]+")
DASH_TRANSLATION = str.maketrans("", "", "-" + HOLD_DASHES)
SYLLABLE_MAP = {
    "a": "阿",
    "da": "嗒",
    "du": "嘟",
    "ei": "诶",
    "gu": "咕",
    "ha": "哈",
    "ho": "嚯",
    "ka": "咔",
    "ku": "库",
    "la": "啦",
    "li": "哩",
    "lu": "噜",
    "luo": "洛",
    "luu": "噜",
    "ma": "嘛",
    "mi": "咪",
    "miu": "咪呜",
    "mo": "摸",
    "mu": "姆",
    "na": "呐",
    "o": "噢",
    "pa": "啪",
    "ra": "啦",
    "ro": "啰",
    "sa": "萨",
    "shu": "舒",
    "wa": "哇",
    "wu": "呜",
    "xi": "西",
    "ya": "呀",
}


class PronunciationError(ValueError):
    pass


@dataclass(frozen=True)
class PronunciationAnalysis:
    generated_text: str
    direction: str
    emphasis: tuple[str, ...]
    emphasis_indices: tuple[int, ...]
    hold_units: tuple[int, ...]


def analyze_pronunciation(pronunciation: str) -> PronunciationAnalysis:
    direction = (
        "rise" if "↗" in pronunciation else "fall" if "↘" in pronunciation else "flat"
    )
    output: list[str] = []
    emphasis: list[str] = []
    emphasis_indices: list[int] = []
    hold_units: list[int] = []

    for token in TOKEN_RE.findall(pronunciation):
        if token.isascii() and token.isalpha():
            mapped = SYLLABLE_MAP.get(token.lower())
            if mapped is None:
                raise PronunciationError(f"没有拟声汉字映射的音节: {token}")
            if token.isupper():
                emphasis.append(token.lower())
                emphasis_indices.append(len(hold_units))
            hold_units.append(0)
            output.append(mapped)
            continue

        _collect_non_ascii_prosody(token, hold_units)
        output.append(_normalize_separator(token))

    text = "".join(output).strip()
    if not text:
        raise PronunciationError("发音标记转换后为空")
    if direction == "rise" and text[-1] not in "？！":
        text += "？"
    elif direction != "rise" and text[-1] not in "。？！……":
        text += "。"
    return PronunciationAnalysis(
        generated_text=text,
        direction=direction,
        emphasis=tuple(emphasis),
        emphasis_indices=tuple(emphasis_indices),
        hold_units=tuple(hold_units),
    )


def strip_pronunciation_dashes(value: str) -> str:
    return value.translate(DASH_TRANSLATION)


def _collect_non_ascii_prosody(token: str, hold_units: list[int]) -> None:
    for character in token:
        if character in HOLD_DASHES:
            if not hold_units:
                raise PronunciationError("延音横杠前缺少可延长的音节")
            units = hold_units[-1] + 1
            if units > MAX_HOLD_UNITS:
                raise PronunciationError(f"单个音节最多使用 {MAX_HOLD_UNITS} 格延音")
            hold_units[-1] = units
        elif _is_cjk(character):
            hold_units.append(0)


def _normalize_separator(value: str) -> str:
    return (
        strip_pronunciation_dashes(value)
        .replace("↗", "")
        .replace("↘", "")
        .replace(",", "，")
        .replace("?", "？")
        .replace("!", "！")
        .replace(" ", "")
    )


def _is_cjk(character: str) -> bool:
    return "\u3400" <= character <= "\u4dbf" or "\u4e00" <= character <= "\u9fff"
