from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SQLiteConnection:
    """Create short-lived SQLite sessions and serialize write transactions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = threading.RLock()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            yield connection

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self._connect() as connection:
            yield connection

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
