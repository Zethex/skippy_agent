"""Conversation history in SQLite (lives on the /data volume)."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, provider TEXT, model TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _run(self, sql: str, args=()) -> list[dict]:
        with self._lock, self._conn:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def create_conversation(self, title: str) -> dict:
        now, cid = time.time(), uuid.uuid4().hex
        self._run("INSERT INTO conversations VALUES (?,?,?,?)", (cid, title, now, now))
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}

    def list_conversations(self) -> list[dict]:
        return self._run("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 200")

    def get_conversation(self, cid: str) -> dict | None:
        rows = self._run("SELECT * FROM conversations WHERE id = ?", (cid,))
        return rows[0] if rows else None

    def rename_conversation(self, cid: str, title: str) -> None:
        self._run("UPDATE conversations SET title = ? WHERE id = ?", (title, cid))

    def delete_conversation(self, cid: str) -> None:
        self._run("DELETE FROM conversations WHERE id = ?", (cid,))

    def add_message(self, cid: str, role: str, content: str, provider: str = "", model: str = "") -> None:
        now = time.time()
        self._run("INSERT INTO messages (conversation_id, role, content, provider, model, created_at) "
                  "VALUES (?,?,?,?,?,?)", (cid, role, content, provider, model, now))
        self._run("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))

    def messages(self, cid: str) -> list[dict]:
        return self._run("SELECT role, content, provider, model, created_at FROM messages "
                         "WHERE conversation_id = ? ORDER BY id", (cid,))
