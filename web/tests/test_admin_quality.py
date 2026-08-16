from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.api.routes import admin_system
from app.database import Database
from app.domain import ScriptItem


def _database(settings_factory):
    settings = settings_factory()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    return settings, database


def _script_item() -> ScriptItem:
    return ScriptItem(1, 1, "text", "mo-la", "摸啦。", "flat", ())


def test_asset_repositories_apply_sql_limits(settings_factory) -> None:
    settings, database = _database(settings_factory)
    item = _script_item()
    for index in range(3):
        voice_id = database.voices.create(
            f"voice-{index}", "owner", "", project_id="project-quality"
        )
        voice_path = settings.data_root / f"voice-{index}.wav"
        voice_path.write_bytes(b"audio")
        database.voices.add_file(
            voice_id, voice_path.name, voice_path, voice_path.stat().st_size
        )
        script_path = settings.data_root / f"script-{index}.txt"
        script_path.write_text("text | mo-la\n", encoding="utf-8")
        database.scripts.create(
            f"script-{index}",
            script_path.name,
            script_path,
            "owner",
            "test",
            [item],
            project_id="project-quality",
        )

    assert len(database.voices.list(limit=2)) == 2
    assert len(database.scripts.list(limit=2)) == 2


def test_admin_assets_treats_all_as_unfiltered_and_clamps_limit(app_client) -> None:
    client, services = app_client
    admin = services.database.auth.get_by_username("admin")
    assert admin
    project = services.database.projects.create(str(admin["id"]), "质量项目", "")
    voice_id = services.database.voices.create(
        "质量声音", str(admin["id"]), "", project_id=str(project["id"])
    )
    voice_path = services.settings.data_root / "quality.wav"
    voice_path.write_bytes(b"audio")
    services.database.voices.add_file(
        voice_id, voice_path.name, voice_path, voice_path.stat().st_size
    )

    response = client.get("/api/admin/assets", params={"project_id": "all", "limit": 0})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["voices"]) == 1
    assert payload["voices"][0]["project_id"] == project["id"]


def test_monitoring_counts_are_zero_safe(settings_factory) -> None:
    _, database = _database(settings_factory)

    counts = database.monitoring.counts()

    assert counts == {
        "voices": 0,
        "scripts": 0,
        "queued": 0,
        "running": 0,
        "candidate_queued": 0,
        "candidate_running": 0,
        "jobs": 0,
        "completed": 0,
        "failed": 0,
    }


def test_directory_size_cache_is_thread_safe_and_refreshable(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "one.bin").write_bytes(b"1")
    admin_system._STORAGE_CACHE.clear()
    monkeypatch.setattr(admin_system, "_STORAGE_CACHE_TTL", 60.0)

    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(admin_system._directory_size, [root] * 4))
    assert values == [1, 1, 1, 1]

    (root / "two.bin").write_bytes(b"22")
    monkeypatch.setattr(admin_system, "_STORAGE_CACHE_TTL", 0.0)
    assert admin_system._directory_size(root) == 3
