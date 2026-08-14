from __future__ import annotations

import json
from pathlib import Path

from app.database import Database
from app.legacy_import import LegacyImporter, legacy_id

from conftest import make_wav_bytes


def test_legacy_import_is_idempotent_and_preserves_display_text(
    settings_factory,
) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    script_path, audio_path, final_path = _legacy_files(settings.root)
    _legacy_configuration(settings.root, script_path, audio_path, final_path)
    database = Database(settings.database_path)
    database.initialize()
    importer = LegacyImporter(database, settings.root)

    assert importer.run() == {
        "voices": 1,
        "voice_files": 1,
        "scripts": 1,
        "legacy_jobs": 1,
    }
    assert LegacyImporter(database, settings.root).run() == {
        "voices": 0,
        "voice_files": 0,
        "scripts": 0,
        "legacy_jobs": 0,
    }

    script_id = legacy_id("legacy-script", str(script_path.resolve()))
    script = database.scripts.get(script_id)
    assert script is not None
    assert script["default_voice_id"] == "suqi"
    assert script["default_effect_id"] == "elder"
    assert Path(script["source_path"]).parent.name == "legacy_display"

    job = database.jobs.get(legacy_id("legacy-job", "demo"))
    assert job is not None
    assert job["status"] == "completed"
    item = database.jobs.items(str(job["id"]))[0]
    assert item["text"] == "正常台词"
    assert item["pronunciation"] == "mo-la"
    candidates = database.candidates.list_for_job(str(job["id"]))
    assert [candidate["id"] for candidate in candidates] == [
        item["accepted_candidate_id"]
    ]
    assert candidates[0]["text"] == "正常台词"


def _legacy_files(root: Path) -> tuple[Path, Path, Path]:
    scripts_root = root / "input" / "scripts"
    voice_root = root / "input" / "voices" / "suqi"
    final_root = root / "output" / "07_generated_final" / "demo"
    scripts_root.mkdir(parents=True)
    voice_root.mkdir(parents=True)
    final_root.mkdir(parents=True)
    script_path = scripts_root / "demo.txt"
    script_path.write_text("mo-la\n", encoding="utf-8")
    audio_path = voice_root / "source.wav"
    audio_path.write_bytes(make_wav_bytes())
    final_path = final_root / "001.wav"
    final_path.write_bytes(make_wav_bytes())
    return script_path, audio_path, final_path


def _legacy_configuration(
    root: Path,
    script_path: Path,
    audio_path: Path,
    final_path: Path,
) -> None:
    voices_csv = root / "input" / "voices" / "voice_source_list.csv"
    voices_csv.write_text(
        "voice_id,audio_path,enabled\nsuqi,suqi/source.wav,1\n",
        encoding="utf-8",
    )
    (root / "input" / "script_voice_map.csv").write_text(
        "script_file,voice_id,processing_profile\ndemo.txt,suqi,yuweng\n",
        encoding="utf-8",
    )
    display_root = root / "input" / "scripts" / "display"
    display_root.mkdir()
    (display_root / "normal_script_guide.md").write_text(
        "## demo\n\n【1 发音】mo-la\n正常台词\n",
        encoding="utf-8",
    )
    manifest = {
        "rows": [
            {
                "script_name": "demo",
                "script_file": str(script_path),
                "voice_id": "suqi",
                "processing_profile": "yuweng",
                "audio_order": 1,
                "source_line": 1,
                "generated_text": "摸啦。",
                "pronunciation": "mo-la",
                "direction": "flat",
                "emphasis": "",
                "status": "existing",
                "final_audio": str(final_path),
                "raw_audio": "",
                "processing_backend": "legacy",
            }
        ]
    }
    manifest_path = root / "output" / "07_generated_final" / "script_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
