from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Absolute paths for the voice workspace and the web runtime."""

    root: Path

    @classmethod
    def from_file(cls, path: Path | None = None) -> "Settings":
        source = (path or Path(__file__)).resolve()
        root = source.parents[2]
        return cls(root=root)

    @property
    def web_root(self) -> Path:
        return self.root / "web"

    @property
    def app_root(self) -> Path:
        return self.web_root / "app"

    @property
    def static_root(self) -> Path:
        return self.web_root / "static"

    @property
    def config_root(self) -> Path:
        return self.web_root / "config"

    @property
    def data_root(self) -> Path:
        return self.web_root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_root / "voice_web.sqlite3"

    @property
    def upload_root(self) -> Path:
        return self.data_root / "uploads"

    @property
    def voice_upload_root(self) -> Path:
        return self.upload_root / "voices"

    @property
    def script_upload_root(self) -> Path:
        return self.upload_root / "scripts"

    @property
    def job_root(self) -> Path:
        return self.data_root / "jobs"

    @property
    def export_root(self) -> Path:
        return self.data_root / "exports"

    @property
    def clone_config_path(self) -> Path:
        return self.root / "code" / "gpt_sovits_config.json"

    @property
    def profiles_path(self) -> Path:
        return self.config_root / "profiles.json"

    @property
    def provider_config_path(self) -> Path:
        return self.data_root / "provider-settings.json"

    @property
    def text_model_config_path(self) -> Path:
        return self.data_root / "text-model-settings.json"

    def ensure_directories(self) -> None:
        for path in (
            self.data_root,
            self.voice_upload_root,
            self.script_upload_root,
            self.job_root,
            self.export_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
