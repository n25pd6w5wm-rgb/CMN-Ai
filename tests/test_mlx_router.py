"""Tests for MLXRouter — learned classify with rule-based fallback + delegation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings
from cmn_ai.core import (
    AgentResponse,
    Bucket,
    Capability,
    Complexity,
    CostPerMTok,
    Task,
)
from cmn_ai.router.mlx_router import MLXRouter
from cmn_ai.router.rule_router import RuleRouter

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
FREE = CostPerMTok(0.0, 0.0)


class FakeAgent:
    def __init__(self, name: str, *, caps: set[Capability], cost: CostPerMTok, bucket: Bucket):
        self.name = name
        self.model = "m"
        self.capabilities = frozenset(caps)
        self.cost_per_mtok = cost
        self.bucket = bucket
        self.active = True

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        raise NotImplementedError


def _governor(tmp_path: Path) -> BudgetGovernor:
    return BudgetGovernor(
        settings=BudgetSettings(monthly_budget_eur=30.0),
        ledger=Ledger(tmp_path / "l.db"),
        now=lambda: NOW,
    )


def test_classify_uses_model_output_when_valid() -> None:
    # rule router would call "hello" chat/low; the model says code/high -> model wins
    router = MLXRouter(
        rule_router=RuleRouter(),
        generate_fn=lambda _p: '{"capability":"code","complexity":"high","needs_web":false}',
    )
    c = router.classify(Task(prompt="hello"))
    assert c.capability is Capability.CODE
    assert c.complexity is Complexity.HIGH
    assert c.bucket is Bucket.CODING


def test_classify_falls_back_to_rules_on_garbage() -> None:
    router = MLXRouter(rule_router=RuleRouter(), generate_fn=lambda _p: "i cannot help")
    c = router.classify(Task(prompt="write a python function"))
    # rule router classifies this as code
    assert c.capability is Capability.CODE


def test_classify_falls_back_to_rules_on_exception() -> None:
    def _boom(_p: str) -> str:
        raise RuntimeError("model unavailable")

    router = MLXRouter(rule_router=RuleRouter(), generate_fn=_boom)
    c = router.classify(Task(prompt="what's the latest news?"))
    assert c.capability is Capability.RESEARCH  # from the rule fallback


def test_select_delegates_identically_to_rule_router(tmp_path: Path) -> None:
    rule = RuleRouter()
    router = MLXRouter(rule_router=rule, generate_fn=lambda _p: "{}")
    agents = {
        "local": FakeAgent(
            "local", caps={Capability.CHAT, Capability.CODE}, cost=FREE, bucket=Bucket.GENERAL
        )
    }
    gov = _governor(tmp_path)
    task = Task(prompt="hi")
    classification = rule.classify(task)

    mlx_decision = router.select(task, classification, agents, gov)
    rule_decision = rule.select(task, classification, agents, gov)
    assert mlx_decision == rule_decision


async def test_optimize_and_synthesize_delegate() -> None:
    router = MLXRouter(rule_router=RuleRouter(), generate_fn=lambda _p: "{}")
    task = Task(prompt="x")
    assert (await router.optimize_prompt(task)) is task
    responses = [AgentResponse(text="only", agent="local", model="m")]
    assert await router.synthesize(task, responses) == "only"
