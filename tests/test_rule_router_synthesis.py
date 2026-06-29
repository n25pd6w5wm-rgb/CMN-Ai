"""Tests for RuleRouter.optimize_prompt and synthesize."""

from __future__ import annotations

from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Message, Task
from cmn_ai.router.rule_router import RuleRouter


class _FakeOptimizer:
    """Minimal Agent stand-in that returns a fixed text or raises."""

    name = "opt"
    model = "gemma"
    capabilities: frozenset[Capability] = frozenset()
    cost_per_mtok = CostPerMTok(0.0, 0.0)
    bucket = Bucket.GENERAL
    active = True

    def __init__(self, *, text: str = "", exc: Exception | None = None) -> None:
        self._text = text
        self._exc = exc
        self.seen_prompt: str | None = None
        self.seen_system: str | None = None

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        self.seen_prompt = task.prompt
        self.seen_system = system
        if self._exc is not None:
            raise self._exc
        return AgentResponse(text=self._text, agent=self.name, model=self.model)


_LONG = "Please help me with the following somewhat involved question about my project " * 2


async def test_optimize_prompt_is_noop_without_optimizer() -> None:
    task = Task(prompt="unchanged")
    assert (await RuleRouter().optimize_prompt(task)) is task


async def test_optimize_prompt_rewrites_substantial_prompt() -> None:
    opt = _FakeOptimizer(text="  a clearer, sharper version  ")
    router = RuleRouter(optimizer=opt)
    history = (Message(role="user", content="earlier"),)
    task = Task(prompt=_LONG, history=history)

    result = await router.optimize_prompt(task)

    assert result.prompt == "a clearer, sharper version"  # stripped
    assert result.history == history  # context preserved
    assert opt.seen_prompt == _LONG  # original prompt sent to optimizer
    assert opt.seen_system is not None  # rewrite instruction provided


async def test_optimize_prompt_skips_trivial_prompt() -> None:
    opt = _FakeOptimizer(text="should not be used")
    task = Task(prompt="hi there")
    assert (await RuleRouter(optimizer=opt).optimize_prompt(task)) is task
    assert opt.seen_prompt is None  # optimizer never called


async def test_optimize_prompt_skips_attachments() -> None:
    opt = _FakeOptimizer(text="should not be used")
    task = Task(prompt=_LONG, has_attachments=True)
    assert (await RuleRouter(optimizer=opt).optimize_prompt(task)) is task
    assert opt.seen_prompt is None


async def test_optimize_prompt_falls_back_on_optimizer_error() -> None:
    opt = _FakeOptimizer(exc=RuntimeError("ollama down"))
    task = Task(prompt=_LONG)
    assert (await RuleRouter(optimizer=opt).optimize_prompt(task)) is task


async def test_optimize_prompt_rejects_empty_rewrite() -> None:
    opt = _FakeOptimizer(text="   ")
    task = Task(prompt=_LONG)
    assert (await RuleRouter(optimizer=opt).optimize_prompt(task)) is task


async def test_optimize_prompt_rejects_runaway_rewrite() -> None:
    opt = _FakeOptimizer(text="x" * (len(_LONG) * 4 + 500))
    task = Task(prompt=_LONG)
    assert (await RuleRouter(optimizer=opt).optimize_prompt(task)) is task


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
