"""Conversation store — persistent multi-turn chats (history + per-message route info).

This is what turns the app from a one-shot prompt box into a real product: every
conversation and its messages are saved in SQLite so the sidebar can list past chats,
reopen them, and feed prior turns back to the model as context. Assistant messages keep
their route metadata (agent/model/cost) so a reopened chat re-renders its transparency
chips. Local SQLite, thread-safe for the web server (same pattern as the decision log).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    ts              REAL NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    agent           TEXT,
    model           TEXT,
    cost_eur        REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
"""

_DEFAULT_TITLE = "New chat"
_TITLE_MAX = 48


def _derive_title(text: str) -> str:
    """A short, single-line title from the first user message."""
    flat = " ".join(text.split())
    return flat[:_TITLE_MAX].rstrip() + ("…" if len(flat) > _TITLE_MAX else "") or _DEFAULT_TITLE


class ConversationStore:
    """Persistent conversations and their messages."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def create(self, *, at: datetime, title: str = _DEFAULT_TITLE) -> int:
        ts = at.timestamp()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, ts, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def list_all(self) -> list[dict[str, Any]]:
        """Conversations, most recently active first, with their message counts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at, "
                "       COUNT(m.id) AS message_count "
                "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
                "GROUP BY c.id ORDER BY c.updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def exists(self, conversation_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            return row is not None

    def messages(self, conversation_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, agent, model, cost_eur, ts FROM messages "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        *,
        at: datetime,
        agent: str | None = None,
        model: str | None = None,
        cost_eur: float | None = None,
    ) -> None:
        """Append a message; bump the conversation's updated_at and auto-title it."""
        ts = at.timestamp()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (conversation_id, ts, role, content, agent, model, cost_eur) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, ts, role, content, agent, model, cost_eur),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conversation_id)
            )
            # Name the chat after its first user message while still untitled.
            if role == "user":
                row = self._conn.execute(
                    "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
                if row is not None and row["title"] == _DEFAULT_TITLE:
                    self._conn.execute(
                        "UPDATE conversations SET title = ? WHERE id = ?",
                        (_derive_title(content), conversation_id),
                    )
            self._conn.commit()

    def rename(self, conversation_id: int, title: str) -> None:
        clean = " ".join(title.split())[:_TITLE_MAX] or _DEFAULT_TITLE
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (clean, conversation_id)
            )
            self._conn.commit()

    def delete(self, conversation_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            self._conn.commit()
