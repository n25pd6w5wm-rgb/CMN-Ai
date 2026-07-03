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


# ---------- failover: a dead agent must not kill the request ----------


class DownAgent(FakeAgent):
    """Agent whose backend is unreachable (e.g. Pi tunnel down)."""

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        self.calls += 1
        raise ConnectionError("host unreachable")


def _down_local() -> DownAgent:
    return DownAgent(
        "local", capabilities={Capability.CHAT, Capability.CODE}, cost=FREE, bucket=Bucket.GENERAL
    )


def _paid_chat(name: str = "anthropic") -> FakeAgent:
    return FakeAgent(
        name,
        capabilities={Capability.CHAT, Capability.CODE},
        cost=CostPerMTok(2.8, 13.8),
        bucket=Bucket.GENERAL,
        reply="paid answer",
    )


async def test_failover_to_next_agent_when_selected_agent_is_down(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    local, paid = _down_local(), _paid_chat()
    orch = Orchestrator(
        agents={"local": local, "anthropic": paid}, router=RuleRouter(), governor=gov
    )
    decision, response = await orch.handle(Task(prompt="hello there"))
    assert local.calls == 1  # tried first (free, low complexity)
    assert response is not None and response.text == "paid answer"
    assert decision.agent == "anthropic"
    assert decision.fell_back is True
    assert "local" in decision.reason  # transparent about what happened
    # the successful paid run is billed, the failed free one is not
    assert gov.status().buckets[Bucket.GENERAL].spent_eur > 0


async def test_failover_raises_when_no_agent_is_left(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    orch = Orchestrator(agents={"local": _down_local()}, router=RuleRouter(), governor=gov)
    try:
        await orch.handle(Task(prompt="hello there"))
    except ConnectionError:
        pass
    else:
        raise AssertionError("expected the original failure to surface")


async def test_no_failover_for_user_selected_agent(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    local, paid = _down_local(), _paid_chat()
    orch = Orchestrator(
        agents={"local": local, "anthropic": paid}, router=RuleRouter(), governor=gov
    )
    try:
        await orch.handle(Task(prompt="hello there"), agent_override="local")
    except ConnectionError:
        pass
    else:
        raise AssertionError("an explicit agent choice must not be silently rerouted")
    assert paid.calls == 0


# ---------- health checks: agents that expose one are refreshed before routing ----------


class HealthAwareAgent(FakeAgent):
    """Fake local agent whose availability is discovered via refresh_health()."""

    def __init__(self, *, healthy: bool) -> None:
        super().__init__(
            "local",
            capabilities={Capability.CHAT, Capability.CODE},
            cost=FREE,
            bucket=Bucket.GENERAL,
        )
        self.active = False  # pessimistic until checked, like the real OllamaAgent
        self._healthy = healthy
        self.refreshes = 0

    async def refresh_health(self) -> None:
        self.refreshes += 1
        self.active = self._healthy


async def test_handle_refreshes_health_checked_agents_before_routing(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    local = HealthAwareAgent(healthy=True)
    orch = Orchestrator(agents={"local": local}, router=RuleRouter(), governor=gov)

    decision, response = await orch.handle(Task(prompt="hello there"))

    assert local.refreshes == 1
    assert decision.agent == "local"
    assert response is not None


async def test_offline_local_routes_to_paid_without_fallback_flag(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    local, paid = HealthAwareAgent(healthy=False), _paid_chat()
    orch = Orchestrator(
        agents={"local": local, "anthropic": paid}, router=RuleRouter(), governor=gov
    )

    decision, response = await orch.handle(Task(prompt="hello there"))

    assert local.calls == 0  # the dead Pi is never even tried
    assert decision.agent == "anthropic"
    assert decision.fell_back is False  # a clean primary decision, not a fallback
    assert response is not None and response.text == "paid answer"
