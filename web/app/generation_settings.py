from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GenerationParameter:
    key: str
    label: str
    hint: str
    default: float | int
    minimum: float
    maximum: float
    step: float
    simple: bool = False
    integer: bool = False

    def normalize(self, value: Any) -> float | int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.label}必须是数字")
        number = float(value)
        if not math.isfinite(number) or not self.minimum <= number <= self.maximum:
            raise ValueError(f"{self.label}需在 {self.minimum:g} 到 {self.maximum:g} 之间")
        return int(round(number)) if self.integer else round(number, 4)

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "hint": self.hint,
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "simple": self.simple,
            "integer": self.integer,
        }


GENERATION_PARAMETERS = (
    GenerationParameter(
        "temperature",
        "表现变化",
        "越高越有变化，越低越稳定；过高可能出现怪异咬字。",
        0.8,
        0.1,
        1.5,
        0.05,
        simple=True,
    ),
    GenerationParameter(
        "speed_factor",
        "生成语速",
        "直接影响 GPT-SoVITS 的生成节奏，1.0 为原速。",
        1.0,
        0.65,
        1.5,
        0.01,
        simple=True,
    ),
    GenerationParameter(
        "top_k",
        "候选词范围",
        "每一步保留的候选数量，越高变化越多。",
        15,
        1,
        100,
        1,
        integer=True,
    ),
    GenerationParameter(
        "top_p",
        "累计概率范围",
        "越低越保守，越高越自由。",
        0.9,
        0.1,
        1.0,
        0.01,
    ),
    GenerationParameter(
        "repetition_penalty",
        "重复抑制",
        "提高可减少重复音节，过高会让发音不连贯。",
        1.25,
        1.0,
        2.0,
        0.01,
    ),
)

PARAMETERS_BY_KEY = {parameter.key: parameter for parameter in GENERATION_PARAMETERS}


def normalize_generation_settings(values: Any) -> dict[str, float | int]:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError("生成参数必须是对象")
    unknown = sorted(set(values) - set(PARAMETERS_BY_KEY))
    if unknown:
        raise ValueError("未知生成参数: " + ", ".join(unknown))
    return {
        key: PARAMETERS_BY_KEY[key].normalize(value) for key, value in values.items()
    }


def public_generation_controls() -> list[dict[str, Any]]:
    return [parameter.public() for parameter in GENERATION_PARAMETERS]


def generation_defaults() -> dict[str, float | int]:
    return {parameter.key: parameter.default for parameter in GENERATION_PARAMETERS}
