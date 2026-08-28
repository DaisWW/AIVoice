from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .domain import ScriptItem
from .persistence import Database
from .persistence.repositories.legacy import LegacyJob
from .script_parser import ScriptFormatError, parse_file, parse_guide
from .storage import ensure_within


EFFECT_IDS = {
    "yugong": "steady_male",
    "chunzhijing": "loli",
    "yanhua": "warm",
    "dazuo": "ethereal",
    "yuweng": "elder",
}
SCRIPT_EXTENSIONS = {".txt", ".md", ".csv", ".docx"}


def legacy_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


class LegacyImporter:
    """Register existing CLI inputs and output without copying source media."""

    def __init__(self, database: Database, root: Path) -> None:
        self._database = database
        self._root = root
        self._counts = {
            "voices": 0,
            "voice_files": 0,
            "scripts": 0,
            "legacy_jobs": 0,
        }

    def run(self) -> dict[str, int]:
        self._counts = {
            "voices": 0,
            "voice_files": 0,
            "scripts": 0,
            "legacy_jobs": 0,
        }
        self._database.legacy.remap_effects()
        self._import_voices()
        guide_items = self._load_guide_items()
        self._import_scripts(guide_items, self._load_script_defaults())
        self._import_jobs()
        self._backfill_job_items(guide_items)
        return dict(self._counts)

    def _import_voices(self) -> None:
        source = self._root / "input" / "voices" / "voice_source_list.csv"
        for row in self._csv_rows(source):
            voice_id = str(row.get("voice_id") or "").strip()
            if not voice_id:
                continue
            if self._database.legacy.add_voice_if_missing(voice_id):
                self._counts["voices"] += 1
            self._import_voice_file(source.parent, voice_id, row)

    def _import_voice_file(
        self, source_root: Path, voice_id: str, row: dict[str, str]
    ) -> None:
        raw_path = str(row.get("audio_path") or "").strip()
        try:
            audio_path = ensure_within(source_root / raw_path, self._root / "input")
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        if not audio_path.is_file():
            return
        file_id = legacy_id("legacy-voice-file", str(audio_path))
        enabled = str(row.get("enabled") or "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "禁用",
        }
        if self._database.legacy.add_voice_file_if_missing(
            file_id, voice_id, audio_path, enabled
        ):
            self._counts["voice_files"] += 1

    def _load_guide_items(self) -> dict[str, list[ScriptItem]]:
        path = self._root / "input" / "scripts" / "display" / "normal_script_guide.md"
        if not path.is_file():
            return {}
        try:
            return parse_guide(path)
        except ScriptFormatError:
            return {}

    def _load_script_defaults(self) -> dict[str, tuple[str | None, str | None]]:
        source = self._root / "input" / "script_voice_map.csv"
        defaults: dict[str, tuple[str | None, str | None]] = {}
        for row in self._csv_rows(source):
            legacy_effect = str(row.get("processing_profile") or "").strip()
            defaults[Path(str(row.get("script_file") or "")).name] = (
                str(row.get("voice_id") or "").strip() or None,
                EFFECT_IDS.get(legacy_effect, legacy_effect or None),
            )
        return defaults

    def _import_scripts(
        self,
        guide_items: dict[str, list[ScriptItem]],
        defaults: dict[str, tuple[str | None, str | None]],
    ) -> None:
        scripts_root = self._root / "input" / "scripts"
        if not scripts_root.is_dir():
            return
        for path in sorted(scripts_root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SCRIPT_EXTENSIONS:
                continue
            self._import_script(path, guide_items.get(path.stem, []), defaults)

    def _import_script(
        self,
        path: Path,
        display_items: list[ScriptItem],
        defaults: dict[str, tuple[str | None, str | None]],
    ) -> None:
        try:
            items = parse_file(path)
        except ScriptFormatError:
            return
        script_id = legacy_id("legacy-script", str(path.resolve()))
        stored_path, items = self._display_script(script_id, path, items, display_items)
        default_voice, default_effect = defaults.get(path.name, (None, None))
        created = self._database.legacy.upsert_script(
            script_id,
            stored_path,
            path.name,
            default_voice,
            default_effect,
            len(items),
        )
        if created:
            self._counts["scripts"] += 1

    def _display_script(
        self,
        script_id: str,
        source_path: Path,
        parsed_items: list[ScriptItem],
        display_items: list[ScriptItem],
    ) -> tuple[Path, list[ScriptItem]]:
        if len(display_items) != len(parsed_items):
            return source_path.resolve(), parsed_items
        target = (
            self._database.path.parent
            / "uploads"
            / "scripts"
            / "legacy_display"
            / f"{script_id}.txt"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._script_text(display_items), encoding="utf-8")
        return target, display_items

    @staticmethod
    def _script_text(items: list[ScriptItem]) -> str:
        lines = [
            f"{item.text.replace(chr(10), ' ')}\t{item.pronunciation}" for item in items
        ]
        return "\n".join(lines) + "\n"

    def _import_jobs(self) -> None:
        path = self._root / "output" / "07_generated_final" / "script_manifest.json"
        for script_name, rows in self._manifest_groups(path).items():
            ordered = [self._normalized_manifest_row(row) for row in rows]
            ordered.sort(key=lambda item: item["audio_order"])
            for row in ordered:
                raw_audio = str(row.get("raw_audio") or "").strip()
                if raw_audio:
                    try:
                        ensure_within(Path(raw_audio), self._root / "output")
                    except (OSError, RuntimeError, TypeError, ValueError):
                        row["raw_audio"] = ""
            first = ordered[0]
            script_path = Path(str(first.get("script_file") or ""))
            job = LegacyJob(
                id=legacy_id("legacy-job", script_name),
                script_id=legacy_id("legacy-script", str(script_path.resolve())),
                voice_id=str(first.get("voice_id") or ""),
                effect_id=EFFECT_IDS.get(
                    str(first.get("processing_profile") or ""), "natural"
                ),
                items=ordered,
            )
            if self._database.legacy.add_job_if_missing(job):
                self._counts["legacy_jobs"] += 1

    @staticmethod
    def _manifest_groups(path: Path) -> dict[str, list[dict[str, Any]]]:
        rows = LegacyImporter._manifest_rows(path)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            final_path = Path(str(row.get("final_audio") or ""))
            try:
                final_path = ensure_within(final_path, path.parent.parent)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            if not final_path.is_file():
                continue
            if str(row.get("status")) not in {"generated", "existing"}:
                continue
            name = str(row.get("script_name") or final_path.parent.name)
            groups[name].append(row)
        return groups

    @staticmethod
    def _normalized_manifest_row(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["audio_order"] = LegacyImporter._safe_int(row.get("audio_order"))
        normalized["source_line"] = LegacyImporter._safe_int(row.get("source_line"))
        raw_audio = str(row.get("raw_audio") or "").strip()
        normalized["raw_audio"] = raw_audio
        return normalized

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _manifest_rows(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        rows = payload.get("rows", [])
        return rows if isinstance(rows, list) else []

    def _backfill_job_items(self, guide_items: dict[str, list[ScriptItem]]) -> None:
        for script_name, items in guide_items.items():
            self._database.legacy.backfill_job_items(script_name, items)

    @staticmethod
    def _csv_rows(path: Path) -> list[dict[str, str]]:
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
