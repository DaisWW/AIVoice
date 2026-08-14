from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ReferenceAudio:
    path: Path
    prompt_text: str
    prompt_lang: str
    duration_seconds: float


@dataclass(frozen=True)
class GenerationResult:
    audio_path: Path
    duration_seconds: float
    processing_backend: str
    elapsed_seconds: float


@dataclass(frozen=True)
class ModelStatus:
    available: bool
    loaded: bool = False
    reason: str = ""
    missing_files: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "loaded": self.loaded,
            "availability_reason": self.reason,
            "missing_files": list(self.missing_files),
        }


class VoiceAdapter(Protocol):
    engine_id: str

    @property
    def is_loaded(self) -> bool:
        ...

    def status(self, profile: dict[str, Any]) -> ModelStatus:
        ...

    def generate(
        self,
        item: dict[str, Any],
        reference: ReferenceAudio,
        profile: dict[str, Any],
        seed: int,
        output_path: Path,
        generation_settings: dict[str, float | int],
    ) -> GenerationResult:
        ...

    def unload(self) -> None:
        ...
