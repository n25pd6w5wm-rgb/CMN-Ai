"""Budget-Governor — hard, rolling weekly spend limits with isolated buckets.

The monthly budget is scaled to a rolling 7-day window (``monthly * 7/30``) and split
across buckets (general / coding). Before any *paid* call, ``can_spend`` checks the
bucket's remaining headroom; free (zero-cost) calls always pass. ``raise_budget``
lifts the monthly budget so weekly caps scale up immediately — the hook a commercial
"buy more credits" action would call. See plan decisions D-2 and D-3.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cmn_ai.budget.ledger import LedgerBackend
from cmn_ai.config import BudgetSettings
from cmn_ai.core import Bucket, Usage

_WINDOW = timedelta(days=7)
# A rolling 7-day window holds 7/30 of a month's budget.
_MONTH_TO_WEEK = 7 / 30


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class BucketStatus:
    bucket: Bucket
    cap_eur: float
    spent_eur: float
    remaining_eur: float


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    monthly_budget_eur: float
    weekly_budget_eur: float
    buckets: dict[Bucket, BucketStatus]


class BudgetGovernor:
    """Enforces rolling weekly spend caps per budget bucket."""

    def __init__(
        self,
        *,
        settings: BudgetSettings,
        ledger: LedgerBackend,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._settings = settings
        self._ledger = ledger
        self._now = now

    @property
    def weekly_budget_eur(self) -> float:
        return self._settings.monthly_budget_eur * _MONTH_TO_WEEK

    def weekly_cap(self, bucket: Bucket) -> float:
        """The rolling weekly EUR cap for a bucket."""
        share = self._settings.bucket_split.get(bucket, 0.0)
        return self.weekly_budget_eur * share

    def spent(self, bucket: Bucket) -> float:
        """EUR spent in the bucket over the last 7 days."""
        return self._ledger.spent_since(bucket, self._now() - _WINDOW)

    def remaining(self, bucket: Bucket) -> float:
        return self.weekly_cap(bucket) - self.spent(bucket)

    def can_spend(self, bucket: Bucket, est_eur: float) -> bool:
        """Whether an estimated paid spend fits the bucket. Free calls always pass."""
        if est_eur <= 0:
            return True
        return est_eur <= self.remaining(bucket)

    def record(
        self,
        bucket: Bucket,
        agent: str,
        model: str,
        usage: Usage,
        *,
        eur: float,
    ) -> None:
        """Book an actual spend into the ledger at the current time."""
        self._ledger.record(
            bucket=bucket,
            agent=agent,
            model=model,
            usage=usage,
            eur=eur,
            at=self._now(),
        )

    def raise_budget(self, new_monthly_eur: float) -> None:
        """Lift the monthly budget; weekly caps scale immediately."""
        self._settings.monthly_budget_eur = new_monthly_eur

    def status(self) -> BudgetStatus:
        """A snapshot of caps, spend and remaining for every bucket."""
        buckets = {
            bucket: BucketStatus(
                bucket=bucket,
                cap_eur=self.weekly_cap(bucket),
                spent_eur=self.spent(bucket),
                remaining_eur=self.remaining(bucket),
            )
            for bucket in self._settings.bucket_split
        }
        return BudgetStatus(
            monthly_budget_eur=self._settings.monthly_budget_eur,
            weekly_budget_eur=self.weekly_budget_eur,
            buckets=buckets,
        )
