"""Tests for the agent factory that wires settings + pricing into adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from cmn_ai.agents.factory import build_agents
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.budget.pricing import price_for
from cmn_ai.config import load_settings
from cmn_ai.core import Bucket, Capability


def test_only_enabled_agents_are_built() -> None:
    settings = load_settings(profile="mac")
    agents = build_agents(settings)
    # default.yaml enables only the local agent; it starts inactive until its
    # health check has actually seen a reachable Ollama.
    assert "local" in agents
    assert agents["local"].active is False
    assert "anthropic" not in agents


def test_enabled_cloud_agent_is_built_with_pricing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = load_settings(profile="mac")
    settings.agents["anthropic"].enabled = True

    agents = build_agents(settings)

    anthropic = agents["anthropic"]
    assert anthropic.name == "anthropic"
    assert anthropic.active is True
    assert anthropic.cost_per_mtok == price_for(settings.agents["anthropic"].model)
    assert Capability.CODE in anthropic.capabilities


def test_coding_agent_uses_coding_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = load_settings(profile="mac")
    settings.agents["coding"].enabled = True

    agents = build_agents(settings)

    assert agents["coding"].name == "coding"
    assert agents["coding"].bucket is Bucket.CODING


def test_coding_tool_loop_is_off_without_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = load_settings(profile="mac")
    settings.agents["coding"].enabled = True

    coding = build_agents(settings)["coding"]

    assert coding._workspace is None  # type: ignore[attr-defined]
    assert coding._budget_guard is None  # type: ignore[attr-defined]


def test_coding_budget_guard_wired_with_workspace_and_governor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = load_settings(profile="mac")
    settings.agents["coding"].enabled = True
    settings.agents["coding"].workspace_root = str(tmp_path)
    governor = BudgetGovernor(settings=settings.budget, ledger=Ledger(tmp_path / "ledger.db"))

    coding = build_agents(settings, governor)["coding"]

    # Guard is active only when a governor is supplied alongside the workspace.
    assert coding._workspace is not None  # type: ignore[attr-defined]
    assert coding._budget_guard is not None  # type: ignore[attr-defined]


def test_cloud_agent_built_but_dormant_without_key() -> None:
    settings = load_settings(profile="mac")
    settings.agents["perplexity"].enabled = True
    # no PERPLEXITY_API_KEY set

    agents = build_agents(settings)

    assert "perplexity" in agents
    assert agents["perplexity"].active is False
    assert Capability.RESEARCH in agents["perplexity"].capabilities


def test_anthropic_advertises_multimodal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = load_settings(profile="mac")
    settings.agents["anthropic"].enabled = True

    agents = build_agents(settings)

    assert Capability.MULTIMODAL in agents["anthropic"].capabilities
