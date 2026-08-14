from __future__ import annotations

from ..common import utc_now
from ..connection import SQLiteConnection


class ClientRepository:
    def __init__(self, database: SQLiteConnection) -> None:
        self._database = database

    def touch(self, client_id: str) -> None:
        timestamp = utc_now()
        with self._database.write() as connection:
            connection.execute(
                """
                INSERT INTO clients(id, first_seen_at, last_seen_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at
                """,
                (client_id, timestamp, timestamp),
            )
