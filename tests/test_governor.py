"""Tests for the Budget-Governor: weekly caps, buckets, enforcement, raise_budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings
from cmn_ai.core import Bucket, Usage

START = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _governor(tmp_path: Path, clock: Clock, monthly: float = 35.0) -> BudgetGovernor:
    settings = BudgetSettings(monthly_budget_eur=monthly)
    ledger = Ledger(tmp_path / "l.db")
    return BudgetGovernor(settings=settings, ledger=ledger, now=clock)


def test_weekly_cap_is_monthly_scaled_and_split(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    # weekly = 30 * 7/30 = 7.0; general 60% = 4.2, coding 40% = 2.8
    assert gov.weekly_cap(Bucket.GENERAL) == pytest.approx(4.2)
    assert gov.weekly_cap(Bucket.CODING) == pytest.approx(2.8)


def test_can_spend_under_cap_but_blocks_over(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    assert gov.can_spend(Bucket.CODING, 2.0) is True
    assert gov.can_spend(Bucket.CODING, 3.0) is False  # cap is 2.8


def test_free_calls_always_allowed_even_when_exhausted(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    gov.record(Bucket.GENERAL, "x", "m", Usage(0, 0), eur=100.0)
    assert gov.can_spend(Bucket.GENERAL, 5.0) is False
    assert gov.can_spend(Bucket.GENERAL, 0.0) is True


def test_recording_reduces_remaining(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    gov.record(Bucket.GENERAL, "x", "m", Usage(0, 0), eur=4.0)
    assert gov.remaining(Bucket.GENERAL) == pytest.approx(0.2)
    assert gov.can_spend(Bucket.GENERAL, 0.3) is False


def test_bucket_isolation(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    gov.record(Bucket.CODING, "x", "m", Usage(0, 0), eur=100.0)
    assert gov.can_spend(Bucket.CODING, 0.5) is False
    assert gov.can_spend(Bucket.GENERAL, 0.5) is True


def test_spend_rolls_off_after_seven_days(tmp_path: Path) -> None:
    clock = Clock(START)
    gov = _governor(tmp_path, clock, monthly=30.0)
    gov.record(Bucket.CODING, "x", "m", Usage(0, 0), eur=2.8)
    assert gov.can_spend(Bucket.CODING, 0.1) is False
    # eight days later, the old spend has rolled out of the 7-day window
    clock.now = START + timedelta(days=8)
    assert gov.can_spend(Bucket.CODING, 2.0) is True


def test_raise_budget_scales_caps_and_reenables(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    gov.record(Bucket.CODING, "x", "m", Usage(0, 0), eur=2.8)
    assert gov.can_spend(Bucket.CODING, 1.0) is False

    gov.raise_budget(60.0)  # doubles monthly → coding weekly cap 5.6
    assert gov.weekly_cap(Bucket.CODING) == pytest.approx(5.6)
    assert gov.can_spend(Bucket.CODING, 2.0) is True


def test_status_reports_caps_and_spend(tmp_path: Path) -> None:
    gov = _governor(tmp_path, Clock(START), monthly=30.0)
    gov.record(Bucket.GENERAL, "x", "m", Usage(0, 0), eur=1.2)
    status = gov.status()
    assert status.monthly_budget_eur == 30.0
    general = status.buckets[Bucket.GENERAL]
    assert general.cap_eur == pytest.approx(4.2)
    assert general.spent_eur == pytest.approx(1.2)
    assert general.remaining_eur == pytest.approx(3.0)
