from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .contracts import ReferenceAudio


class ReferenceAudioBuilder:
    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        self._root = root
        self._config = config

    def build(
        self, voice_files: list[dict[str, Any]], target_path: Path
    ) -> ReferenceAudio:
        if not voice_files:
            raise ValueError("所选声音库没有启用的录音")
        audio = self._audio_module()
        records = [self._record(item, index) for index, item in enumerate(voice_files)]
        reference = self._config["voice_clone"]["reference"]
        path, _, duration, prompt_text, prompt_lang = audio.build_reference_audio(
            records,
            target_path,
            int(self._config["reference_sample_rate"]),
            float(reference.get("target_seconds", 6.0)),
            float(reference.get("max_seconds", 9.5)),
            float(reference.get("gap_ms", 120)) / 1000.0,
            self._config["trim"],
        )
        first = voice_files[0]
        return ReferenceAudio(
            path,
            prompt_text,
            prompt_lang,
            duration,
            str(first.get("voice_id") or ""),
            str(first.get("voice_name") or ""),
            tuple(Path(item["source_path"]) for item in voice_files),
        )

    def _audio_module(self) -> Any:
        code_root = str(self._root / "code")
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
        import gpt_sovits_audio

        return gpt_sovits_audio

    @staticmethod
    def _record(item: dict[str, Any], index: int) -> dict[str, Any]:
        return {
            "voice_id": item["voice_id"],
            "order": index + 1,
            "enabled": True,
            "audio_path": Path(item["source_path"]),
            "reference_text": str(item.get("reference_text") or ""),
            "reference_lang": str(item.get("reference_lang") or "all_zh"),
            "notes": "",
        }
