from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.uploads import (
    ScriptStorage,
    VoiceFileStorage,
)
from app.api.smart_script_import import (
    SmartImportSource,
    SmartScriptImport,
    SmartScriptImportError,
    _extract_smart_import_units,
    _prepare_smart_import_confirmation,
    _smart_import_analysis_chunks,
    _validate_smart_import_segments,
)
from app.domain import MAX_SCRIPT_ITEMS, ScriptItem
from app.storage import UPLOAD_CHUNK_SIZE, read_upload


class _Upload:
    def __init__(self, filename: str, chunks: list[bytes]) -> None:
        self.filename = filename
        self._chunks = iter(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return next(self._chunks, b"")


def test_read_upload_uses_bounded_chunks() -> None:
    upload = _Upload("lines.txt", [b"one", b"two"])

    content = asyncio.run(read_upload(upload))

    assert content == b"onetwo"
    assert upload.read_sizes == [UPLOAD_CHUNK_SIZE] * 3


def test_load_items_accepts_explicit_empty_script(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("text,pronunciation\n", encoding="utf-8")
    services = SimpleNamespace(settings=SimpleNamespace(root=tmp_path))
    storage = ScriptStorage(services)

    assert storage.load_items({"item_count": 0, "source_path": str(path)}) == []


def test_read_upload_rejects_oversized_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.storage.MAX_UPLOAD_BYTES", 3)
    upload = _Upload("lines.txt", [b"1234"])

    with pytest.raises(ValueError, match="200 MB"):
        asyncio.run(read_upload(upload))


def test_import_rejects_more_than_3000_items(monkeypatch: pytest.MonkeyPatch) -> None:
    services = SimpleNamespace(settings=SimpleNamespace(root=Path.cwd()))
    storage = ScriptStorage(services)
    upload = _Upload("lines.txt", [b"content"])
    monkeypatch.setattr(
        storage,
        "_parse_content",
        lambda content, suffix: [object()] * (MAX_SCRIPT_ITEMS + 1),
    )
    script = {"id": "script", "source_path": str(Path.cwd() / "script.txt")}

    with pytest.raises(HTTPException) as raised:
        asyncio.run(storage.replace_from_upload(script, upload))

    assert raised.value.status_code == 422
    assert "3000" in str(raised.value.detail)


def test_new_script_rejects_more_than_3000_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Scripts:
        @staticmethod
        def create(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("oversized script was persisted")

    services = SimpleNamespace(
        settings=SimpleNamespace(script_upload_root=tmp_path, root=tmp_path),
        database=SimpleNamespace(scripts=Scripts()),
    )
    storage = ScriptStorage(services)
    monkeypatch.setattr(
        storage,
        "_parse",
        lambda path: [object()] * (MAX_SCRIPT_ITEMS + 1),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            storage.store(_Upload("lines.txt", [b"content"]), "owner", "project")
        )

    assert raised.value.status_code == 422
    assert "3000" in str(raised.value.detail)
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_voice_upload_rejects_non_finite_quality_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Normalizer:
        @staticmethod
        def normalize(source: Path) -> Path:
            normalized = source.with_suffix(".wav")
            source.replace(normalized)
            return normalized

    class Analyzer:
        @staticmethod
        def analyze(path: Path) -> dict[str, object]:
            return {"duration_seconds": "NaN"}

    class Voices:
        @staticmethod
        def add_files(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("invalid quality was persisted")

    services = SimpleNamespace(
        settings=SimpleNamespace(
            root=tmp_path,
            voice_upload_root=tmp_path / "uploads",
        ),
        database=SimpleNamespace(voices=Voices()),
    )
    storage = VoiceFileStorage(services)
    storage._normalizer = Normalizer()
    monkeypatch.setattr("app.api.uploads.AudioQualityAnalyzer", Analyzer)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(storage.store("voice", [_Upload("sample.wav", [b"audio"])]))

    assert raised.value.status_code == 422
    assert "质量检测结果无效" in str(raised.value.detail)
    assert not [path for path in (tmp_path / "uploads").rglob("*") if path.is_file()]


def test_save_items_serializes_writes_and_uses_unique_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = tmp_path / "source.txt"
    old_path.write_text("old", encoding="utf-8")
    updates: list[Path] = []
    temporary_paths: list[Path] = []

    class Database:
        class Scripts:
            @staticmethod
            def update_source(script_id, path, count, *, original_name=None):
                updates.append(path)
                return True

        scripts = Scripts()

    services = SimpleNamespace(
        settings=SimpleNamespace(root=tmp_path),
        database=Database(),
        script_write_lock=threading.Lock(),
    )
    storage = ScriptStorage(services)

    def write_csv(path: Path, items: list[ScriptItem]) -> None:
        temporary_paths.append(path)
        time.sleep(0.02)
        path.write_text("text,pronunciation\n", encoding="utf-8")

    monkeypatch.setattr(storage, "_write_csv", write_csv)
    script = {"id": "script", "source_path": str(old_path)}
    threads = [
        threading.Thread(target=storage.save_items, args=(script, [])) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(updates) == 2
    assert len({path.name for path in temporary_paths}) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_save_items_restores_existing_file_when_database_update_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "script.edited.csv"
    target.write_text("previous content", encoding="utf-8")

    class Scripts:
        @staticmethod
        def update_source(*args, **kwargs):
            raise RuntimeError("database unavailable")

    services = SimpleNamespace(
        settings=SimpleNamespace(root=tmp_path),
        database=SimpleNamespace(scripts=Scripts()),
        script_write_lock=threading.Lock(),
    )
    storage = ScriptStorage(services)

    with pytest.raises(RuntimeError, match="database unavailable"):
        storage.save_items({"id": "script", "source_path": str(target)}, [])

    assert target.read_text(encoding="utf-8") == "previous content"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_save_items_preserves_existing_file_when_csv_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "script.edited.csv"
    target.write_text("previous content", encoding="utf-8")

    class Scripts:
        @staticmethod
        def update_source(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("database update must not run")

    services = SimpleNamespace(
        settings=SimpleNamespace(root=tmp_path),
        database=SimpleNamespace(scripts=Scripts()),
        script_write_lock=threading.Lock(),
    )
    storage = ScriptStorage(services)
    monkeypatch.setattr(
        storage,
        "_write_csv",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        storage.save_items({"id": "script", "source_path": str(target)}, [])

    assert target.read_text(encoding="utf-8") == "previous content"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_smart_import_preserves_existing_lines_and_pronunciation_rows() -> None:
    units = _extract_smart_import_units(
        [
            SmartImportSource(
                "roles.txt",
                "甲：第一句\n乙：第二句\n".encode(),
            ),
            SmartImportSource(
                "marked.txt",
                "显示台词 | mo-la\n".encode(),
            ),
        ]
    )

    assert [unit.existing_segment for unit in units] == [True, True, True]
    assert units[2].text == "显示台词"
    assert units[2].pronunciation == "mo-la"


def test_smart_import_only_splits_unformatted_single_line_text() -> None:
    units = _extract_smart_import_units(
        [
            SmartImportSource("single.txt", "只是一句完整台词。".encode()),
            SmartImportSource("roles.txt", "第一幕甲：你好。乙：我来了。".encode()),
        ]
    )

    assert units[0].existing_segment is True
    assert units[1].existing_segment is False


def test_smart_import_batches_long_existing_scripts_for_model_analysis() -> None:
    units = _extract_smart_import_units(
        [
            SmartImportSource(
                "many-lines.txt",
                "\n".join(f"第 {index} 句" for index in range(85)).encode(),
            )
        ]
    )

    chunks = _smart_import_analysis_chunks(units)

    assert [len(chunk) for chunk in chunks] == [40, 40, 5]
    assert [unit.id for chunk in chunks for unit in chunk] == list(range(1, 86))


def test_smart_import_rejects_splitting_an_existing_segment() -> None:
    units = _extract_smart_import_units(
        [SmartImportSource("roles.txt", "甲：第一句\n乙：第二句\n".encode())]
    )
    analysis = {
        "segments": [
            {
                "unit_id": 1,
                "start": 0,
                "end": 2,
                "content_start": 0,
                "script": "第一幕",
                "speaker": "甲",
                "kind": "dialogue",
            },
            {
                "unit_id": 1,
                "start": 2,
                "end": len(units[0].text),
                "content_start": 2,
                "script": "第一幕",
                "speaker": "甲",
                "kind": "dialogue",
            },
            {
                "unit_id": 2,
                "start": 0,
                "end": len(units[1].text),
                "content_start": 2,
                "script": "第一幕",
                "speaker": "乙",
                "kind": "dialogue",
            },
        ]
    }

    with pytest.raises(SmartScriptImportError, match="拆分已有分段"):
        _validate_smart_import_segments(units, analysis)


def test_smart_import_rejects_missing_source_characters() -> None:
    units = _extract_smart_import_units(
        [SmartImportSource("raw.txt", "甲：你好。乙：来了。".encode())]
    )
    analysis = {
        "segments": [
            {
                "unit_id": 1,
                "start": 0,
                "end": 5,
                "content_start": 0,
                "script": "",
                "speaker": "",
                "kind": "note",
            },
            {
                "unit_id": 1,
                "start": 6,
                "end": len(units[0].text),
                "content_start": 6,
                "script": "",
                "speaker": "",
                "kind": "note",
            },
        ]
    }

    with pytest.raises(SmartScriptImportError, match="遗漏、重叠或乱序"):
        _validate_smart_import_segments(units, analysis)


def test_smart_import_rejects_non_object_confirmation_selection() -> None:
    batch = {"drafts": [{"id": "draft-1", "lines": [{"text": "你好"}]}]}

    with pytest.raises(SmartScriptImportError, match="选择无效"):
        _prepare_smart_import_confirmation(batch, [None])  # type: ignore[list-item]


def test_smart_import_removes_temporary_manifest_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = SimpleNamespace(settings=SimpleNamespace(data_root=tmp_path))
    importer = SmartScriptImport(services)
    monkeypatch.setattr(
        "app.api.smart_script_import.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        importer._write_manifest("project", "user", {"batch": {}})

    assert not list(tmp_path.rglob("*.tmp"))


def test_smart_import_confirmation_rejects_changed_name_after_partial_create(
    tmp_path: Path,
) -> None:
    class Scripts:
        @staticmethod
        def get(script_id: str) -> dict[str, str]:
            return {
                "id": script_id,
                "project_id": "project",
                "owner_id": "user",
                "source_kind": "ai_import",
                "name": "旧名称",
            }

    services = SimpleNamespace(
        settings=SimpleNamespace(data_root=tmp_path),
        database=SimpleNamespace(scripts=Scripts()),
        job_mutation_lock=threading.Lock(),
        script_write_lock=threading.Lock(),
    )
    importer = SmartScriptImport(services)
    batch = {
        "batch_id": "import-one",
        "source_files": ["source.txt"],
        "drafts": [
            {
                "id": "draft-1",
                "name": "旧名称",
                "script": "",
                "speaker": "",
                "source_files": ["source.txt"],
                "line_count": 1,
                "lines": [
                    {
                        "text": "你好",
                        "pronunciation": "你好",
                        "source_file": "source.txt",
                    }
                ],
            }
        ],
        "dialogue_line_count": 1,
        "excluded": [],
        "excluded_count": 0,
    }
    importer._write_manifest(
        "project",
        "user",
        {
            "version": 1,
            "project_id": "project",
            "user_id": "user",
            "batch": batch,
        },
    )

    with pytest.raises(SmartScriptImportError, match="名称已发生变化"):
        importer.confirm(
            "project",
            "user",
            "import-one",
            [{"id": "draft-1", "name": "新名称"}],
        )

    assert importer._manifest_path("project", "user").exists()


def test_smart_import_restores_existing_script_when_database_create_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "scripts" / "project" / "script-fixed.ai-import.csv"
    target.parent.mkdir(parents=True)
    target.write_text("previous content", encoding="utf-8")

    class Scripts:
        @staticmethod
        def create(*args, **kwargs):
            raise RuntimeError("database unavailable")

    services = SimpleNamespace(
        settings=SimpleNamespace(
            root=tmp_path,
            data_root=tmp_path / "data",
            script_upload_root=tmp_path / "scripts",
        ),
        database=SimpleNamespace(scripts=Scripts()),
    )
    importer = SmartScriptImport(services)

    with pytest.raises(RuntimeError, match="database unavailable"):
        importer._create_script(
            "project",
            "user",
            "script-fixed",
            "导入台本",
            [ScriptItem(1, 1, "你好", "你好", "你好", "flat", ())],
        )

    assert target.read_text(encoding="utf-8") == "previous content"
    assert not list(target.parent.glob(".*.tmp"))
    assert not list(target.parent.glob(".*.bak"))
