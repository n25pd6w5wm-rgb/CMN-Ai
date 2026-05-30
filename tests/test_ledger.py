"""Tests for the SQLite spend ledger (rolling-window accounting, persistence)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cmn_ai.budget.ledger import Ledger
from cmn_ai.core import Bucket, Usage

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


def _record(ledger: Ledger, bucket: Bucket, eur: float, at: datetime) -> None:
    ledger.record(
        bucket=bucket,
        agent="anthropic",
        model="claude-sonnet-4-6",
        usage=Usage(100, 200),
        eur=eur,
        at=at,
    )


def test_spent_since_sums_entries_in_window(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.db")
    _record(ledger, Bucket.GENERAL, 1.0, NOW)
    _record(ledger, Bucket.GENERAL, 2.5, NOW)
    assert ledger.spent_since(Bucket.GENERAL, NOW - timedelta(days=7)) == 3.5


def test_entries_before_window_are_excluded(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.db")
    _record(ledger, Bucket.GENERAL, 5.0, NOW - timedelta(days=8))
    _record(ledger, Bucket.GENERAL, 2.0, NOW)
    assert ledger.spent_since(Bucket.GENERAL, NOW - timedelta(days=7)) == 2.0


def test_buckets_are_isolated(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.db")
    _record(ledger, Bucket.GENERAL, 3.0, NOW)
    _record(ledger, Bucket.CODING, 4.0, NOW)
    since = NOW - timedelta(days=7)
    assert ledger.spent_since(Bucket.GENERAL, since) == 3.0
    assert ledger.spent_since(Bucket.CODING, since) == 4.0


def test_spend_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "l.db"
    first = Ledger(path)
    _record(first, Bucket.CODING, 7.0, NOW)

    reopened = Ledger(path)
    assert reopened.spent_since(Bucket.CODING, NOW - timedelta(days=7)) == 7.0


def test_empty_window_is_zero(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.db")
    assert ledger.spent_since(Bucket.GENERAL, NOW - timedelta(days=7)) == 0.0
