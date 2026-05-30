"""Tests for RuleRouter.optimize_prompt and synthesize."""

from __future__ import annotations

from cmn_ai.core import AgentResponse, Task
from cmn_ai.router.rule_router import RuleRouter


async def test_optimize_prompt_is_noop_passthrough() -> None:
    task = Task(prompt="unchanged")
    assert (await RuleRouter().optimize_prompt(task)) is task


async def test_synthesize_single_response_passthrough() -> None:
    responses = [AgentResponse(text="only answer", agent="local", model="m")]
    assert await RuleRouter().synthesize(Task(prompt="q"), responses) == "only answer"


async def test_synthesize_empty_is_empty_string() -> None:
    assert await RuleRouter().synthesize(Task(prompt="q"), []) == ""


async def test_synthesize_joins_multiple_responses() -> None:
    responses = [
        AgentResponse(text="a", agent="x", model="m"),
        AgentResponse(text="b", agent="y", model="m"),
    ]
    result = await RuleRouter().synthesize(Task(prompt="q"), responses)
    assert "a" in result and "b" in result and "---" in result
