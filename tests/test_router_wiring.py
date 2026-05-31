"""Tests for build_router strategy selection + graceful MLX fallback."""

from __future__ import annotations

from cmn_ai.config import RouterSettings, Settings
from cmn_ai.router.rule_router import RuleRouter
from cmn_ai.web.app import build_router


def test_rule_strategy_returns_rule_router() -> None:
    settings = Settings(profile="mac", router=RouterSettings(strategy="rule"))
    assert isinstance(build_router(settings), RuleRouter)


def test_mlx_strategy_without_adapter_falls_back_to_rule() -> None:
    settings = Settings(
        profile="mac",
        router=RouterSettings(strategy="mlx", adapter_path="/nonexistent/router-adapter"),
    )
    # no adapter on disk -> must not crash, falls back to the rule router
    assert isinstance(build_router(settings), RuleRouter)
