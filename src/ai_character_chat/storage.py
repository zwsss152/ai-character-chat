"""SQLite storage for local sessions, completed turns and generation traces."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .characters import Character


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SessionNotFoundError(Exception):
    pass


class TraceNotFoundError(Exception):
    pass


class SessionStore:
    def __init__(self, database_path: str):
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported session database version")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    character_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    request_id TEXT,
                    PRIMARY KEY(session_id, position)
                );
                CREATE TABLE IF NOT EXISTS traces (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    input_message TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    result_json TEXT,
                    PRIMARY KEY(session_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS sessions_character ON sessions(character_id);
                PRAGMA user_version = 1;
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _summary(row) -> dict:
        return {key: row[key] for key in (
            "id", "character_id", "character_name", "created_at", "updated_at"
        )}

    def create(self, character: Character) -> dict:
        session_id, timestamp = str(uuid4()), utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, character.id, character.name, character.model_dump_json(),
                 timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO messages VALUES (?, 0, 'assistant', ?, NULL)",
                (session_id, character.greeting),
            )
        return self.get(session_id)

    def list(self, character_id: str | None = None) -> list[dict]:
        with self.connection() as connection:
            sql = "SELECT * FROM sessions"
            parameters: tuple = ()
            if character_id is not None:
                sql += " WHERE character_id = ?"
                parameters = (character_id,)
            rows = connection.execute(
                sql + " ORDER BY updated_at DESC, rowid DESC", parameters
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get(self, session_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(session_id)
            messages = connection.execute(
                "SELECT role, content, request_id FROM messages "
                "WHERE session_id = ? ORDER BY position", (session_id,)
            ).fetchall()
        return {**self._summary(row), "messages": [dict(item) for item in messages]}

    def character(self, session_id: str) -> Character:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT character_json FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return Character.model_validate_json(row["character_json"])

    def previous_request(self, session_id: str, request_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT input_message, result_json FROM traces "
                "WHERE session_id = ? AND request_id = ?", (session_id, request_id)
            ).fetchone()
        if row is None:
            return None
        return {"message": row["input_message"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None}

    def record(self, session_id: str, message: str, trace: dict,
               result: dict | None = None) -> None:
        """Commit a completed turn and its trace together; failures store only a trace."""
        with self.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone() is None:
                raise SessionNotFoundError(session_id)
            if result is not None:
                position = connection.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                connection.executemany(
                    "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                    [(session_id, position, "user", message, trace["request_id"]),
                     (session_id, position + 1, "assistant", result["reply"], trace["request_id"])],
                )
                connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (trace["created_at"], session_id),
                )
            connection.execute(
                "INSERT INTO traces VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, request_id) DO UPDATE SET "
                "input_message=excluded.input_message, trace_json=excluded.trace_json, "
                "result_json=excluded.result_json",
                (session_id, trace["request_id"], message,
                 json.dumps(trace, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False) if result is not None else None),
            )

    def trace(self, session_id: str, request_id: str) -> dict:
        self.get(session_id)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT trace_json FROM traces WHERE session_id = ? AND request_id = ?",
                (session_id, request_id),
            ).fetchone()
        if row is None:
            raise TraceNotFoundError(request_id)
        return json.loads(row["trace_json"])

    def delete(self, session_id: str) -> None:
        with self.connection() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise SessionNotFoundError(session_id)
