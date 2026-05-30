"""Tests for the decision log — every routing decision recorded for training."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cmn_ai.core import (
    AgentResponse,
    Bucket,
    Capability,
    Classification,
    Complexity,
    RouteDecision,
    Usage,
)
from cmn_ai.storage.decisions import DecisionLog

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


def _classification(
    cap: Capability = Capability.CHAT, bucket: Bucket = Bucket.GENERAL
) -> Classification:
    return Classification(capability=cap, complexity=Complexity.LOW, needs_web=False, bucket=bucket)


def _decision(agent: str = "local", *, blocked: bool = False) -> RouteDecision:
    return RouteDecision(
        classification=_classification(),
        agent=agent,
        model="gemma4:latest",
        reason="r",
        estimated_eur=0.0,
        blocked=blocked,
    )


def _response(cost: float = 0.0) -> AgentResponse:
    return AgentResponse(
        text="hi", agent="local", model="gemma4:latest", usage=Usage(10, 5), cost_eur=cost
    )


def test_logged_decision_is_retrievable(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.db")
    log.log("hello", _decision(), _response(), at=NOW)
    rows = log.recent()
    assert len(rows) == 1
    assert rows[0]["prompt"] == "hello"
    assert rows[0]["agent"] == "local"
    assert rows[0]["capability"] == "chat"
    assert rows[0]["cost_eur"] == 0.0


def test_blocked_decision_logged_without_response(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.db")
    log.log("do hard thing", _decision("coding", blocked=True), None, at=NOW)
    rows = log.recent()
    assert rows[0]["blocked"] == 1
    assert rows[0]["tokens_out"] == 0


def test_recent_is_newest_first_and_limited(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.db")
    for i in range(5):
        log.log(f"q{i}", _decision(), _response(), at=NOW)
    rows = log.recent(limit=3)
    assert len(rows) == 3
    assert rows[0]["prompt"] == "q4"


def test_summary_counts_and_spend(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.db")
    log.log("a", _decision("local"), _response(0.0), at=NOW)
    log.log("b", _decision("coding"), _response(0.5), at=NOW)
    log.log("c", _decision("coding"), _response(0.5), at=NOW)
    summary = log.summary()
    by_agent = {s["agent"]: s for s in summary["by_agent"]}
    assert by_agent["coding"]["count"] == 2
    assert by_agent["coding"]["spend_eur"] == 1.0
    assert summary["total_eur"] == 1.0
    assert summary["total_count"] == 3


def test_log_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "d.db"
    DecisionLog(path).log("hello", _decision(), _response(), at=NOW)
    assert len(DecisionLog(path).recent()) == 1
