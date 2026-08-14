from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Profiles:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._models = {item["id"]: item for item in data.get("models", [])}
        if not self._models:
            raise ValueError("profiles.json 至少需要一个声音克隆模型")

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

    def public(self) -> dict[str, Any]:
        keys = ("id", "label", "description")
        return {
            "models": [
                {key: item.get(key, "") for key in keys}
                for item in self._models.values()
            ],
        }
