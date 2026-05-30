"""The orchestrator records every decision into the decision log."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage
from cmn_ai.orchestrator import Orchestrator
from cmn_ai.router.rule_router import RuleRouter
from cmn_ai.storage.decisions import DecisionLog

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
FREE = CostPerMTok(0.0, 0.0)


class FakeLocal:
    def __init__(self) -> None:
        self.name = "local"
        self.model = "gemma4:latest"
        self.capabilities = frozenset({Capability.CHAT, Capability.CODE})
        self.cost_per_mtok = FREE
        self.bucket = Bucket.GENERAL
        self.active = True

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        return AgentResponse(
            text="hi", agent="local", model=self.model, usage=Usage(7, 3), cost_eur=0.0
        )


def _orch(tmp_path: Path, log: DecisionLog) -> Orchestrator:
    gov = BudgetGovernor(
        settings=BudgetSettings(monthly_budget_eur=30.0),
        ledger=Ledger(tmp_path / "l.db"),
        now=lambda: NOW,
    )
    return Orchestrator(
        agents={"local": FakeLocal()},
        router=RuleRouter(),
        governor=gov,
        decision_log=log,
        now=lambda: NOW,
    )


async def test_handle_logs_the_decision(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.db")
    orch = _orch(tmp_path, log)
    await orch.handle(Task(prompt="hello there"))
    rows = log.recent()
    assert len(rows) == 1
    assert rows[0]["prompt"] == "hello there"
    assert rows[0]["agent"] == "local"
    assert rows[0]["tokens_out"] == 3
