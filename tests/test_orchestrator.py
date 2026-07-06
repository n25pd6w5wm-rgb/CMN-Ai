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


# ---------- council: multiple AIs answer together, one merges ----------


_PAID_COST = CostPerMTok(2.8, 13.8)


def _paid(name: str, reply: str, cost: CostPerMTok = _PAID_COST) -> FakeAgent:
    return FakeAgent(
        name,
        capabilities={Capability.CHAT, Capability.CODE},
        cost=cost,
        bucket=Bucket.GENERAL,
        reply=reply,
    )


class MergeAgent(FakeAgent):
    """Records what it was asked to merge so the test can assert it saw both drafts."""

    def __init__(self, name: str) -> None:
        super().__init__(
            name,
            capabilities={Capability.CHAT, Capability.CODE},
            cost=CostPerMTok(2.8, 13.8),
            bucket=Bucket.GENERAL,
            reply="MERGED",
        )
        self.seen = ""

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        self.seen = task.prompt
        return await super().run(task, system=system)


async def test_council_runs_several_agents_and_merges(tmp_path: Path) -> None:
    gov = _governor(tmp_path, monthly=1000.0)
    a, b = _paid("gemini", "Entwurf A"), _paid("openai", "Entwurf B")
    merger = MergeAgent("anthropic")
    orch = Orchestrator(
        agents={"gemini": a, "openai": b, "anthropic": merger}, router=RuleRouter(), governor=gov
    )

    decision, response = await orch.handle_council(Task(prompt="Erkläre erneuerbare Energie"))

    assert response is not None
    assert decision.agent == "council"
    assert a.calls == 1 and b.calls == 1  # both drafts ran
    assert "Entwurf A" in merger.seen and "Entwurf B" in merger.seen  # merger saw both
    assert response.text == "MERGED"
    # every contributor is named and the cost is the sum of all calls
    for name in ("gemini", "openai", "anthropic"):
        assert name in decision.model
    assert response.cost_eur > 0


async def test_council_degrades_to_single_when_one_agent(tmp_path: Path) -> None:
    gov = _governor(tmp_path, monthly=1000.0)
    only = _paid("gemini", "nur ich")
    orch = Orchestrator(agents={"gemini": only}, router=RuleRouter(), governor=gov)

    _decision, response = await orch.handle_council(Task(prompt="hallo"))

    assert response is not None and response.text == "nur ich"
    assert only.calls == 1  # no merge call — just the single answer


async def test_council_works_without_local(tmp_path: Path) -> None:
    # local is down (inactive); the paid panel still collaborates
    gov = _governor(tmp_path, monthly=1000.0)
    local = _local()
    local.active = False
    a, b = _paid("gemini", "A"), _paid("openai", "B")
    orch = Orchestrator(
        agents={"local": local, "gemini": a, "openai": b}, router=RuleRouter(), governor=gov
    )

    _decision, response = await orch.handle_council(Task(prompt="frage"))

    assert response is not None
    assert local.calls == 0  # the dead local model is never asked
    assert a.calls >= 1 and b.calls >= 1  # the paid panel collaborated


async def test_council_skips_failed_agents(tmp_path: Path) -> None:
    gov = _governor(tmp_path, monthly=1000.0)
    good, bad = _paid("gemini", "A"), _down_local()
    bad.name = "openai"
    orch = Orchestrator(agents={"gemini": good, "openai": bad}, router=RuleRouter(), governor=gov)

    _decision, response = await orch.handle_council(Task(prompt="frage"))

    # only the good draft survives → returned directly (no merge over one draft)
    assert response is not None and response.text == "A"


async def test_council_prefers_strong_diverse_panel(tmp_path: Path) -> None:
    # with a full roster, the team is strong + diverse (Claude + Perplexity + GPT),
    # not the three cheapest — so cheap flash is not what carries the collaboration.
    gov = _governor(tmp_path, monthly=1000.0)
    agents = {
        "gemini": FakeAgent(
            "gemini",
            capabilities={Capability.CHAT},
            cost=CostPerMTok(0.28, 2.3),
            bucket=Bucket.GENERAL,
            reply="g",
        ),
        "openai": _paid("openai", "o"),
        "anthropic": _paid("anthropic", "a"),
        "perplexity": FakeAgent(
            "perplexity",
            capabilities={Capability.RESEARCH, Capability.CHAT},
            cost=CostPerMTok(2.8, 13.8),
            bucket=Bucket.GENERAL,
            reply="p",
        ),
    }
    orch = Orchestrator(agents=agents, router=RuleRouter(), governor=gov)

    decision, response = await orch.handle_council(Task(prompt="was ist der sinn des lebens"))

    assert response is not None
    assert "anthropic" in decision.model and "perplexity" in decision.model
    assert "gemini" not in decision.model  # the weakest cheap model steps aside
