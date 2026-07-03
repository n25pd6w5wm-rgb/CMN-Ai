"""Tests for RuleRouter.select — the budget-aware routing policy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings, OnLimit
from cmn_ai.core import (
    AgentResponse,
    Bucket,
    Capability,
    Classification,
    Complexity,
    CostPerMTok,
    Task,
    Usage,
)
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
        active: bool = True,
        model: str = "m",
    ) -> None:
        self.name = name
        self.model = model
        self.capabilities = frozenset(capabilities)
        self.cost_per_mtok = cost
        self.bucket = bucket
        self.active = active

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        raise NotImplementedError


def _governor(tmp_path: Path, monthly: float = 30.0) -> BudgetGovernor:
    return BudgetGovernor(
        settings=BudgetSettings(monthly_budget_eur=monthly),
        ledger=Ledger(tmp_path / "l.db"),
        now=lambda: NOW,
    )


def _local() -> FakeAgent:
    return FakeAgent(
        "local",
        capabilities={Capability.CHAT, Capability.CODE},
        cost=FREE,
        bucket=Bucket.GENERAL,
    )


def _coding() -> FakeAgent:
    return FakeAgent(
        "coding",
        capabilities={Capability.CODE},
        cost=CostPerMTok(2.8, 13.8),
        bucket=Bucket.CODING,
    )


def _perplexity(active: bool = True) -> FakeAgent:
    return FakeAgent(
        "perplexity",
        capabilities={Capability.RESEARCH, Capability.CHAT},
        cost=CostPerMTok(2.8, 13.8),
        bucket=Bucket.GENERAL,
        active=active,
    )


def _c(cap: Capability, comp: Complexity, bucket: Bucket, web: bool = False) -> Classification:
    return Classification(capability=cap, complexity=comp, needs_web=web, bucket=bucket)


def test_low_complexity_chat_routes_to_free_local(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="hi"),
        _c(Capability.CHAT, Complexity.LOW, Bucket.GENERAL),
        {"local": _local()},
        _governor(tmp_path),
    )
    assert decision.agent == "local"
    assert decision.estimated_eur == 0.0
    assert decision.blocked is False


def test_high_complexity_code_routes_to_coding_agent(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="design a complex system"),
        _c(Capability.CODE, Complexity.HIGH, Bucket.CODING),
        {"local": _local(), "coding": _coding()},
        _governor(tmp_path),
    )
    assert decision.agent == "coding"
    assert decision.estimated_eur > 0


def test_research_routes_to_perplexity(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="latest news?"),
        _c(Capability.RESEARCH, Complexity.LOW, Bucket.GENERAL, web=True),
        {"local": _local(), "perplexity": _perplexity()},
        _governor(tmp_path),
    )
    assert decision.agent == "perplexity"


def test_research_blocked_when_budget_exhausted(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    gov.record(Bucket.GENERAL, "x", "m", Usage(0, 0), eur=100.0)
    router = RuleRouter(on_limit=OnLimit.BLOCK)
    decision = router.select(
        Task(prompt="latest news?"),
        _c(Capability.RESEARCH, Complexity.LOW, Bucket.GENERAL, web=True),
        {"local": _local(), "perplexity": _perplexity()},
        gov,
    )
    assert decision.blocked is True
    assert decision.agent == "perplexity"


def test_fallback_free_when_budget_exhausted(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    gov.record(Bucket.CODING, "x", "m", Usage(0, 0), eur=100.0)
    router = RuleRouter(on_limit=OnLimit.FALLBACK_FREE)
    decision = router.select(
        Task(prompt="hard code task"),
        _c(Capability.CODE, Complexity.HIGH, Bucket.CODING),
        {"local": _local(), "coding": _coding()},
        gov,
    )
    assert decision.agent == "local"
    assert decision.fell_back is True
    assert decision.blocked is False


def test_no_paid_agent_falls_back_to_local(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="hard code task"),
        _c(Capability.CODE, Complexity.HIGH, Bucket.CODING),
        {"local": _local()},  # no coding agent
        _governor(tmp_path),
    )
    assert decision.agent == "local"
    assert decision.fell_back is True


def test_research_blocked_when_no_local_can_serve(tmp_path: Path) -> None:
    # perplexity inactive (no key) and local cannot do research
    router = RuleRouter()
    decision = router.select(
        Task(prompt="latest news?"),
        _c(Capability.RESEARCH, Complexity.LOW, Bucket.GENERAL, web=True),
        {"local": _local(), "perplexity": _perplexity(active=False)},
        _governor(tmp_path),
    )
    assert decision.blocked is True
    assert decision.agent == ""


# ---------- paid selection: preference by task type, not just cheapest ----------


def _gemini(active: bool = True) -> FakeAgent:
    return FakeAgent(
        "gemini",
        capabilities={Capability.CHAT, Capability.MULTIMODAL},
        cost=CostPerMTok(0.28, 2.3),
        bucket=Bucket.GENERAL,
        active=active,
    )


def _anthropic() -> FakeAgent:
    return FakeAgent(
        "anthropic",
        capabilities={Capability.CHAT, Capability.CODE, Capability.MULTIMODAL},
        cost=CostPerMTok(2.8, 13.8),
        bucket=Bucket.GENERAL,
    )


def _openai() -> FakeAgent:
    return FakeAgent(
        "openai",
        capabilities={Capability.CHAT, Capability.CODE},
        cost=CostPerMTok(0.69, 4.14),
        bucket=Bucket.GENERAL,
    )


def test_low_chat_picks_cheapest_paid(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="hi"),
        _c(Capability.CHAT, Complexity.LOW, Bucket.GENERAL),
        {"gemini": _gemini(), "anthropic": _anthropic(), "openai": _openai()},
        _governor(tmp_path),
    )
    assert decision.agent == "gemini"


def test_high_chat_prefers_sonnet_over_cheaper_models(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="analyse this architecture in depth"),
        _c(Capability.CHAT, Complexity.HIGH, Bucket.GENERAL),
        {"gemini": _gemini(), "anthropic": _anthropic(), "openai": _openai()},
        _governor(tmp_path),
    )
    assert decision.agent == "anthropic"
    assert decision.blocked is False


def test_high_chat_uses_openai_when_sonnet_unaffordable(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    # leave just enough headroom for the cheap model, not for sonnet
    remaining = gov.status().buckets[Bucket.GENERAL].cap_eur - 0.01
    gov.record(Bucket.GENERAL, "x", "m", Usage(0, 0), eur=remaining)
    router = RuleRouter()
    decision = router.select(
        Task(prompt="analyse this architecture in depth"),
        _c(Capability.CHAT, Complexity.HIGH, Bucket.GENERAL),
        {"gemini": _gemini(), "anthropic": _anthropic(), "openai": _openai()},
        gov,
    )
    assert decision.agent == "openai"
    assert decision.blocked is False


def test_code_prefers_coding_bucket_over_general_paid(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="refactor this complex module"),
        _c(Capability.CODE, Complexity.HIGH, Bucket.CODING),
        {"coding": _coding(), "anthropic": _anthropic(), "openai": _openai()},
        _governor(tmp_path),
    )
    assert decision.agent == "coding"


def test_multimodal_prefers_gemini_then_anthropic(tmp_path: Path) -> None:
    router = RuleRouter()
    agents = {"gemini": _gemini(), "anthropic": _anthropic(), "openai": _openai()}
    decision = router.select(
        Task(prompt="what is in this image?", has_attachments=True),
        _c(Capability.MULTIMODAL, Complexity.LOW, Bucket.GENERAL),
        agents,
        _governor(tmp_path),
    )
    assert decision.agent == "gemini"

    without_gemini = {"gemini": _gemini(active=False), "anthropic": _anthropic()}
    decision = router.select(
        Task(prompt="what is in this image?", has_attachments=True),
        _c(Capability.MULTIMODAL, Complexity.LOW, Bucket.GENERAL),
        without_gemini,
        _governor(tmp_path),
    )
    assert decision.agent == "anthropic"


def test_all_unaffordable_blocks_on_top_preference(tmp_path: Path) -> None:
    gov = _governor(tmp_path)
    gov.record(Bucket.GENERAL, "x", "m", Usage(0, 0), eur=100.0)
    router = RuleRouter(on_limit=OnLimit.BLOCK)
    decision = router.select(
        Task(prompt="analyse this architecture in depth"),
        _c(Capability.CHAT, Complexity.HIGH, Bucket.GENERAL),
        {"gemini": _gemini(), "anthropic": _anthropic(), "openai": _openai()},
        gov,
    )
    assert decision.blocked is True
    assert decision.agent == "anthropic"  # the honest top preference, not the cheapest


def test_no_agent_at_all_blocks_with_honest_reason(tmp_path: Path) -> None:
    router = RuleRouter()
    decision = router.select(
        Task(prompt="hi"),
        _c(Capability.CHAT, Complexity.LOW, Bucket.GENERAL),
        {
            "local": FakeAgent(
                "local",
                capabilities={Capability.CHAT},
                cost=FREE,
                bucket=Bucket.GENERAL,
                active=False,
            )
        },
        _governor(tmp_path),
    )
    assert decision.blocked is True
    assert "budget" not in decision.reason
    assert "no agent available" in decision.reason
