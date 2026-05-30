"""Tests for the Orchestrator: classify -> select -> run -> record spend."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage
from cmn_ai.orchestrator import Orchestrator
from cmn_ai.router.rule_router import RuleRouter

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
FREE = CostPerMTok(0.0, 0.0)


class FakeAgent:
    def __init__(
        self,
        name: str,
        *,
        capabilities: set[Capability],
        cost: CostPerMTok,
        bucket: Bucket,
        reply: str = "answer",
    ) -> None:
        self.name = name
        self.model = "m"
        self.capabilities = frozenset(capabilities)
        self.cost_per_mtok = cost
        self.bucket = bucket
        self.active = True
        self._reply = reply
        self.calls = 0

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        self.calls += 1
        usage = Usage(1_000_000, 1_000_000)
        return AgentResponse(
            text=self._reply,
            agent=self.name,
            model=self.model,
            usage=usage,
            cost_eur=self.cost_per_mtok.estimate(usage.tokens_in, usage.tokens_out),
            bucket=self.bucket,
        )


def _governor(tmp_path: Path, monthly: float = 30.0) -> BudgetGovernor:
    return BudgetGovernor(
        settings=BudgetSettings(monthly_budget_eur=monthly),
        ledger=Ledger(tmp_path / "l.db"),
        now=lambda: NOW,
    )


def _local() -> FakeAgent:
    return FakeAgent(
        "local", capabilities={Capability.CHAT, Capability.CODE}, cost=FREE, bucket=Bucket.GENERAL
    )


def _coding() -> FakeAgent:
    return FakeAgent(
        "coding",
        capabilities={Capability.CODE},
        cost=CostPerMTok(2.8, 13.8),
        bucket=Bucket.CODING,
        reply="here is code",
    )


async def test_chat_routes_to_local_and_records_zero(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    orch = Orchestrator(agents={"local": _local()}, router=RuleRouter(), governor=gov)
    decision, response = await orch.handle(Task(prompt="hello there"))
    assert decision.agent == "local"
    assert response is not None
    assert response.text == "answer"
    assert gov.spent(Bucket.GENERAL) == 0.0


async def test_hard_code_routes_to_coding_and_records_spend(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    coding = _coding()
    orch = Orchestrator(
        agents={"local": _local(), "coding": coding}, router=RuleRouter(), governor=gov
    )
    decision, response = await orch.handle(Task(prompt="refactor this complex module"))
    assert decision.agent == "coding"
    assert coding.calls == 1
    assert response is not None
    # 1M*2.8 + 1M*13.8 = 16.6 recorded into the coding bucket
    assert gov.spent(Bucket.CODING) == 16.6
    assert gov.spent(Bucket.GENERAL) == 0.0


async def test_blocked_decision_runs_no_agent_and_records_nothing(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    gov.record(Bucket.CODING, "x", "m", Usage(0, 0), eur=1000.0)
    coding = _coding()
    orch = Orchestrator(agents={"coding": coding}, router=RuleRouter(), governor=gov)
    decision, response = await orch.handle(Task(prompt="refactor this complex module"))
    assert decision.blocked is True
    assert response is None
    assert coding.calls == 0
