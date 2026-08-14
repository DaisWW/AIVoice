from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .generation_settings import (
    PARAMETERS_BY_KEY,
    generation_defaults,
    normalize_generation_settings,
)


class Profiles:
    def __init__(self, data: dict[str, Any]) -> None:
        models = data.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("profiles.json 至少需要一个声音克隆模型")
        self._models: dict[str, dict[str, Any]] = {}
        for item in models:
            if not isinstance(item, dict):
                raise ValueError("声音克隆模型配置必须是对象")
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                raise ValueError("声音克隆模型缺少 id")
            if model_id in self._models:
                raise ValueError(f"声音克隆模型 id 重复: {model_id}")
            parameter_keys = self._parameter_keys(item)
            overrides = normalize_generation_settings(item.get("clone_overrides", {}))
            unsupported = sorted(set(overrides) - set(parameter_keys))
            if unsupported:
                raise ValueError(
                    f"模型 {model_id} 配置了不支持的生成参数: " + ", ".join(unsupported)
                )
            self._models[model_id] = {
                **item,
                "id": model_id,
                "engine": str(item.get("engine") or "gpt_sovits_v2"),
                "generation_parameters": parameter_keys,
                "clone_overrides": overrides,
            }

    @classmethod
    def load(
        cls,
        path: Path,
    ) -> "Profiles":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def model(self, model_id: str) -> dict[str, Any]:
        try:
            return self._models[model_id]
        except KeyError as error:
            raise ValueError(f"未知模型配置: {model_id}") from error

    def generation_settings(self, model_id: str) -> dict[str, float | int]:
        model = self.model(model_id)
        defaults = generation_defaults()
        return {
            key: model["clone_overrides"].get(key, defaults[key])
            for key in model["generation_parameters"]
        }

    def resolve_generation_settings(
        self,
        model_id: str,
        *layers: Any,
    ) -> dict[str, float | int]:
        model = self.model(model_id)
        allowed = set(model["generation_parameters"])
        resolved = self.generation_settings(model_id)
        for values in layers:
            normalized = normalize_generation_settings(values)
            unsupported = sorted(set(normalized) - allowed)
            if unsupported:
                raise ValueError("当前模型不支持参数: " + ", ".join(unsupported))
            resolved.update(normalized)
        return resolved

    def all(self) -> list[dict[str, Any]]:
        return list(self._models.values())

    def public(
        self, statuses: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        statuses = statuses or {}
        keys = (
            "id",
            "label",
            "description",
            "engine",
            "stage",
            "license",
            "workflow",
            "availability_reason",
        )
        return {
            "models": [
                {
                    **{key: item.get(key, "") for key in keys},
                    "generation_defaults": self.generation_settings(model_id),
                    "generation_parameters": list(item["generation_parameters"]),
                    **self._public_status(item, statuses.get(model_id, {})),
                }
                for model_id, item in self._models.items()
            ],
        }

    @staticmethod
    def _public_status(item: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
        configured_available = item.get("available", True)
        available = bool(status.get("available", configured_available))
        if configured_available is False:
            available = False
            reason = item.get("availability_reason") or status.get(
                "availability_reason"
            )
        else:
            reason = status.get("availability_reason") or item.get(
                "availability_reason"
            )
        return {
            "available": available,
            "loaded": bool(status.get("loaded", False)),
            "availability_reason": str(reason or ""),
        }

    @staticmethod
    def _parameter_keys(item: dict[str, Any]) -> list[str]:
        configured = item.get("generation_parameters")
        if configured is None:
            return list(PARAMETERS_BY_KEY)
        if not isinstance(configured, list) or any(
            not isinstance(key, str) for key in configured
        ):
            raise ValueError("generation_parameters 必须是参数名数组")
        unknown = sorted(set(configured) - set(PARAMETERS_BY_KEY))
        if unknown:
            raise ValueError("未知生成参数: " + ", ".join(unknown))
        if len(configured) != len(set(configured)):
            raise ValueError("generation_parameters 不能包含重复参数")
        return configured
