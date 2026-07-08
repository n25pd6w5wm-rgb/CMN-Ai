"""SQLite spend ledger — the durable record of every paid call.

Each paid model call is appended as a row with its bucket, model, token counts, cost
and timestamp. The governor queries ``spent_since`` over a rolling window to enforce
limits. Timestamps are stored as UTC epoch seconds for cheap range comparison.
Persistence is on disk so budget survives restarts.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from cmn_ai.core import Bucket, Usage


@runtime_checkable
class LedgerBackend(Protocol):
    """Interface both the SQLite and Supabase ledgers satisfy."""

    def record(
        self, *, bucket: Bucket, agent: str, model: str, usage: Usage, eur: float, at: datetime
    ) -> None: ...
    def spent_since(self, bucket: Bucket, since: datetime) -> float: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    bucket     TEXT    NOT NULL,
    agent      TEXT    NOT NULL,
    model      TEXT    NOT NULL,
    tokens_in  INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    eur        REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_bucket_ts ON spend (bucket, ts);
"""


class Ledger:
    """Append-only spend log backed by SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the web server may touch the ledger from
        # different worker threads. A lock serializes all access for safety.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(
        self,
        *,
        bucket: Bucket,
        agent: str,
        model: str,
        usage: Usage,
        eur: float,
        at: datetime,
    ) -> None:
        """Append a spend entry."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO spend (ts, bucket, agent, model, tokens_in, tokens_out, eur)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    at.timestamp(),
                    bucket.value,
                    agent,
                    model,
                    usage.tokens_in,
                    usage.tokens_out,
                    eur,
                ),
            )
            self._conn.commit()

    def spent_since(self, bucket: Bucket, since: datetime) -> float:
        """Total EUR spent in ``bucket`` at or after ``since``."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(eur), 0.0) FROM spend WHERE bucket = ? AND ts >= ?",
                (bucket.value, since.timestamp()),
            )
            (total,) = cur.fetchone()
        return float(total)

    def close(self) -> None:
        self._conn.close()
