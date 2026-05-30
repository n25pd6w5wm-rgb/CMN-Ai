"""Decision log — every routing decision and its outcome, stored for training.

This is the dataset the plan's future ``TrainedRouter`` learns from (plan D-5): the
prompt, the classification the heuristic router produced, which agent was chosen, and
the real cost/tokens of the result. Kept deliberately flat and queryable so it can be
exported later. Local SQLite, thread-safe for the web server.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from cmn_ai.core import AgentResponse, RouteDecision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    prompt        TEXT    NOT NULL,
    capability    TEXT    NOT NULL,
    complexity    TEXT    NOT NULL,
    needs_web     INTEGER NOT NULL,
    bucket        TEXT    NOT NULL,
    agent         TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    estimated_eur REAL    NOT NULL,
    fell_back     INTEGER NOT NULL,
    blocked       INTEGER NOT NULL,
    cost_eur      REAL    NOT NULL,
    tokens_in     INTEGER NOT NULL,
    tokens_out    INTEGER NOT NULL
);
"""

_COLUMNS = (
    "ts, prompt, capability, complexity, needs_web, bucket, agent, model, "
    "estimated_eur, fell_back, blocked, cost_eur, tokens_in, tokens_out"
)


class DecisionLog:
    """Append-only log of routing decisions and their measured outcomes."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def log(
        self,
        prompt: str,
        decision: RouteDecision,
        response: AgentResponse | None,
        *,
        at: datetime,
    ) -> None:
        c = decision.classification
        cost = response.cost_eur if response else 0.0
        tin = response.usage.tokens_in if response else 0
        tout = response.usage.tokens_out if response else 0
        with self._lock:
            self._conn.execute(
                f"INSERT INTO decisions ({_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    at.timestamp(),
                    prompt,
                    c.capability.value,
                    c.complexity.value,
                    int(c.needs_web),
                    c.bucket.value,
                    decision.agent,
                    decision.model,
                    decision.estimated_eur,
                    int(decision.fell_back),
                    int(decision.blocked),
                    cost,
                    tin,
                    tout,
                ),
            )
            self._conn.commit()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            agent_rows = self._conn.execute(
                "SELECT agent, COUNT(*) AS count, COALESCE(SUM(cost_eur), 0.0) AS spend_eur"
                " FROM decisions GROUP BY agent ORDER BY count DESC"
            ).fetchall()
            totals = self._conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(cost_eur), 0.0) AS s FROM decisions"
            ).fetchone()
        return {
            "by_agent": [dict(row) for row in agent_rows],
            "total_count": totals["c"],
            "total_eur": float(totals["s"]),
        }
