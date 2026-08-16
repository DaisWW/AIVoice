from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace


WEB_ROOT = Path(__file__).resolve().parents[1]
if str(WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_ROOT))

from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402


class PreviewSettings(Settings):
    @property
    def data_root(self) -> Path:
        return Path(os.environ["VOICE_LAB_PREVIEW_DATA_ROOT"])


class PreviewEngine:
    def __init__(self, settings, profiles, provider_config) -> None:
        del settings, provider_config
        self._models = {
            item["id"]: {
                "id": item["id"],
                "label": item.get("label", item["id"]),
                "available": True,
                "loaded": False,
            }
            for item in profiles.all()
        }

    def model_status(self) -> dict:
        return {"loaded": False, "models": self._models}

    def model_available(self, model_id: str) -> SimpleNamespace:
        return SimpleNamespace(available=model_id in self._models, reason="")


app = create_app(
    PreviewSettings(WEB_ROOT.parent),
    engine_factory=PreviewEngine,
    seed_legacy=False,
)
