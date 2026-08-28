from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.api.cleanup import (
    remove_job_artifacts,
    remove_job_exports,
    remove_script_exports,
)
from app.api.payloads import JobPresenter
from app.database import Database
from app.legacy_import import LegacyImporter, legacy_id
from app.persistence.repositories.projects import LEGACY_PROJECT_ID
from app.profiles import Profiles

from conftest import make_wav_bytes


def test_legacy_project_is_not_created_without_legacy_assets(settings_factory) -> None:
    database, admin = _database_with_admin(settings_factory)

    database.projects.ensure_legacy_project(str(admin["id"]))
    database.projects.ensure_legacy_project(str(admin["id"]))

    assert database.projects.get(LEGACY_PROJECT_ID) is None
    assert database.projects.list_for_user(str(admin["id"])) == []


def test_legacy_project_is_not_recreated_after_modern_project_exists(
    settings_factory,
) -> None:
    database, admin = _database_with_admin(settings_factory)
    database.projects.create(str(admin["id"]), "新项目", "")

    database.projects.ensure_legacy_project(str(admin["id"]))

    assert database.projects.get(LEGACY_PROJECT_ID) is None


def test_legacy_project_claims_unassigned_assets(settings_factory) -> None:
    database, admin = _database_with_admin(settings_factory)
    database.projects.create(str(admin["id"]), "新项目", "")
    voice_id = database.voices.create("旧声音", str(admin["id"]), "")

    database.projects.ensure_legacy_project(str(admin["id"]))
    database.projects.ensure_legacy_project(str(admin["id"]))

    legacy = database.projects.get(LEGACY_PROJECT_ID)
    assert legacy is not None
    assert legacy["owner_id"] == admin["id"]
    assert database.projects.role(LEGACY_PROJECT_ID, str(admin["id"])) == "owner"
    assert database.voices.get(voice_id)["project_id"] == LEGACY_PROJECT_ID


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
    presenter = JobPresenter(
        SimpleNamespace(
            database=database,
            profiles=Profiles.load(settings.profiles_path),
        )
    )
    assert presenter.payload(job)["output_type"] == "gpt_sovits_raw"
    item = database.jobs.items(str(job["id"]))[0]
    assert item["text"] == "正常台词"
    assert item["pronunciation"] == "mo-la"
    candidates = database.candidates.list_for_job(str(job["id"]))
    assert [candidate["id"] for candidate in candidates] == [
        item["accepted_candidate_id"]
    ]
    assert candidates[0]["text"] == "正常台词"


def test_legacy_import_ignores_dirty_order_and_outside_paths(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    script_path, audio_path, final_path = _legacy_files(settings.root)
    _legacy_configuration(settings.root, script_path, audio_path, final_path)
    (settings.root / "input" / "voices" / "outside.wav").write_bytes(make_wav_bytes())
    (settings.root / "input" / "voices" / "voice_source_list.csv").write_text(
        "voice_id,audio_path,enabled\nsuqi,../outside.wav,1\n", encoding="utf-8"
    )
    manifest_path = (
        settings.root / "output" / "07_generated_final" / "script_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rows"][0]["audio_order"] = "not-a-number"
    manifest["rows"][0]["raw_audio"] = str(
        settings.root / "input" / "voices" / "outside.wav"
    )
    outside_row = dict(manifest["rows"][0])
    outside_row["final_audio"] = str(settings.root / "input" / "voices" / "outside.wav")
    manifest["rows"].append(outside_row)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    database = Database(settings.database_path)
    database.initialize()
    counts = LegacyImporter(database, settings.root).run()

    assert counts == {"voices": 1, "voice_files": 0, "scripts": 1, "legacy_jobs": 1}
    job = database.jobs.get(legacy_id("legacy-job", "demo"))
    item = database.jobs.items(str(job["id"]))[0]
    assert item["sequence"] == 0
    assert item["raw_audio_path"] == ""


def test_cleanup_rejects_unsafe_ids(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    sentinel = settings.job_root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    services = SimpleNamespace(settings=settings, export_lock=threading.RLock())

    for value in ("", ".", "..", "nested/job", "C:\\outside", "/tmp/outside"):
        remove_job_artifacts(services, value)
        remove_job_exports(services, value)
        remove_script_exports(services, value)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_cleanup_removes_interrupted_export_temps(settings_factory) -> None:
    settings = settings_factory()
    settings.ensure_directories()
    services = SimpleNamespace(settings=settings, export_lock=threading.RLock())
    job_id = "job-1"
    script_id = "script-1"
    for name in (
        f"{job_id}.zip.tmp",
        f"{job_id}-accepted.zip.tmp",
        f"{script_id}-accepted.zip.tmp",
        f"{script_id}-all.zip.tmp",
    ):
        path = settings.export_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stale")

    remove_job_exports(services, job_id)
    remove_script_exports(services, script_id)

    assert not list(settings.export_root.glob("*.zip.tmp"))


def _database_with_admin(settings_factory) -> tuple[Database, dict[str, Any]]:
    settings = settings_factory()
    database = Database(settings.database_path)
    database.initialize()
    admin = database.auth.create_user(
        "admin", "管理员", "hash", must_change_password=False
    )
    return database, admin


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
