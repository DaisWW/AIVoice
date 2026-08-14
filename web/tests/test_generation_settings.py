from __future__ import annotations

import pytest

from app.generation_settings import PARAMETERS_BY_KEY, normalize_generation_settings
from app.profiles import Profiles


def test_profiles_reject_unknown_generation_override() -> None:
    with pytest.raises(ValueError, match="未知生成参数"):
        Profiles(
            {
                "models": [
                    {
                        "id": "broken",
                        "label": "Broken",
                        "clone_overrides": {"temperatur": 0.8},
                    }
                ]
            }
        )


def test_profiles_reject_duplicate_model_id() -> None:
    with pytest.raises(ValueError, match="id 重复"):
        Profiles(
            {
                "models": [
                    {"id": "duplicate", "label": "First"},
                    {"id": "duplicate", "label": "Second"},
                ]
            }
        )


def test_profiles_store_normalized_model_id() -> None:
    profiles = Profiles({"models": [{"id": " normalized "}]})

    assert profiles.model("normalized")["id"] == "normalized"
    assert profiles.public()["models"][0]["id"] == "normalized"


def test_integer_generation_parameter_is_not_silently_rounded() -> None:
    with pytest.raises(ValueError, match="必须是整数"):
        normalize_generation_settings({"top_k": 1.6})


def test_profile_values_align_with_control_steps() -> None:
    profiles = Profiles(
        {
            "models": [
                {
                    "id": "stable",
                    "clone_overrides": {"temperature": 0.72},
                },
                {
                    "id": "expressive",
                    "clone_overrides": {"temperature": 0.86},
                },
            ]
        }
    )

    for model in profiles.public()["models"]:
        for key, value in model["generation_defaults"].items():
            parameter = PARAMETERS_BY_KEY[key]
            steps = (float(value) - parameter.minimum) / parameter.step
            assert steps == pytest.approx(round(steps))


def test_model_only_accepts_declared_generation_parameters() -> None:
    profiles = Profiles(
        {
            "models": [
                {
                    "id": "speed-only",
                    "generation_parameters": ["speed_factor"],
                    "clone_overrides": {"speed_factor": 0.95},
                }
            ]
        }
    )

    assert profiles.generation_settings("speed-only") == {"speed_factor": 0.95}
    with pytest.raises(ValueError, match="当前模型不支持参数"):
        profiles.resolve_generation_settings("speed-only", {"temperature": 0.8})

    with pytest.raises(ValueError, match="生成参数必须是对象"):
        profiles.resolve_generation_settings("speed-only", [])


def test_profile_rejects_override_for_unsupported_parameter() -> None:
    with pytest.raises(ValueError, match="配置了不支持"):
        Profiles(
            {
                "models": [
                    {
                        "id": "broken-capability",
                        "generation_parameters": ["speed_factor"],
                        "clone_overrides": {"top_k": 20},
                    }
                ]
            }
        )


def test_profile_public_payload_exposes_model_capabilities_and_availability() -> None:
    profiles = Profiles(
        {
            "models": [
                {
                    "id": "optional-model",
                    "engine": "optional-engine",
                    "generation_parameters": ["speed_factor"],
                    "available": False,
                    "availability_reason": "尚未安装",
                }
            ]
        }
    )

    model = profiles.public(
        {
            "optional-model": {
                "available": True,
                "availability_reason": "运行时已安装",
            }
        }
    )["models"][0]
    assert model["engine"] == "optional-engine"
    assert model["generation_parameters"] == ["speed_factor"]
    assert model["generation_defaults"] == {"speed_factor": 1.0}
    assert model["available"] is False
    assert model["availability_reason"] == "尚未安装"
