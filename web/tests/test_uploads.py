from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.uploads import ScriptStorage
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
