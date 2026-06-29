"""Dedicated cloud-coding agent (plan D-4).

Claude is used for code, with model tiering: Sonnet by default, escalating to Opus
for hard tasks (long prompts, code fences, architecture/refactor work). It always
draws on the isolated ``coding`` budget bucket so coding can never starve general use.
A coding-tuned system prompt is sent as a cached prefix to cut repeat input cost.

When a read-only ``workspace`` is supplied the agent runs an **agentic tool loop**: it
hands Claude file-inspection tools (read/list/search, see ``workspace.py``) and keeps
exchanging tool calls until the model produces a final answer. The loop is bounded by a
hard ``max_iterations`` cap and an optional ``budget_guard`` callback (cumulative cost
estimate -> may continue) so a multi-turn request can never run away on the coding
budget. Without a workspace it stays a single Messages-API call (the original behaviour).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import (
    MessageParam,
    TextBlock,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
)

from cmn_ai.agents.base import build_messages
from cmn_ai.agents.workspace import WorkspaceTools
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage

CODING_SYSTEM = (
    "You are cmn-ai's coding expert. Write correct, idiomatic, production-quality code. "
    "Prefer clear, minimal solutions; explain only what matters. When editing existing "
    "code, preserve its style. If requirements are ambiguous, state your assumptions."
)

_HARD_KEYWORDS = (
    "architecture",
    "design",
    "refactor",
    "optimize",
    "distributed",
    "concurren",
    "debug",
    "prove",
    "migrate",
)
_HARD_LENGTH = 400


def _looks_hard(prompt: str) -> bool:
    lower = prompt.lower()
    return "```" in prompt or len(prompt) > _HARD_LENGTH or any(k in lower for k in _HARD_KEYWORDS)


class CodingAgent:
    """Claude-backed coding agent with Sonnet/Opus tiering on the coding bucket."""

    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str,
        hard_model: str,
        price_lookup: Callable[[str], CostPerMTok],
        max_tokens: int = 8192,
        workspace: WorkspaceTools | None = None,
        max_iterations: int = 8,
        budget_guard: Callable[[float], bool] | None = None,
    ) -> None:
        self.name = "coding"
        self.api_key = api_key
        self.model = default_model
        self._hard_model = hard_model
        self._price_lookup = price_lookup
        self.cost_per_mtok = price_lookup(default_model)
        self.capabilities = frozenset({Capability.CODE})
        self.bucket = Bucket.CODING
        self._max_tokens = max_tokens
        self._workspace = workspace
        self._max_iterations = max_iterations
        self._budget_guard = budget_guard

    @property
    def active(self) -> bool:
        return bool(self.api_key)

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        model = self._hard_model if _looks_hard(task.prompt) else self.model
        client = AsyncAnthropic(api_key=self.api_key)
        messages = cast(list[MessageParam], build_messages(task))
        system_blocks: list[TextBlockParam] = [
            {"type": "text", "text": CODING_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ]

        if self._workspace is None:
            message = await client.messages.create(
                model=model, max_tokens=self._max_tokens, messages=messages, system=system_blocks
            )
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            usage = Usage(
                tokens_in=message.usage.input_tokens, tokens_out=message.usage.output_tokens
            )
            return self._response(model, text, usage)

        return await self._run_tool_loop(client, model, messages, system_blocks)

    async def _run_tool_loop(
        self,
        client: AsyncAnthropic,
        model: str,
        messages: list[MessageParam],
        system_blocks: list[TextBlockParam],
    ) -> AgentResponse:
        assert self._workspace is not None
        price = self._price_lookup(model)
        total_in = 0
        total_out = 0
        final_text = ""

        for _ in range(self._max_iterations):
            if self._budget_guard is not None and not self._budget_guard(
                price.estimate(total_in, total_out)
            ):
                final_text = (final_text + "\n\n[stopped: coding budget reached]").strip()
                break

            message = await client.messages.create(
                model=model,
                max_tokens=self._max_tokens,
                messages=messages,
                system=system_blocks,
                tools=cast("list[ToolParam]", self._workspace.tools),
            )
            total_in += message.usage.input_tokens
            total_out += message.usage.output_tokens
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            if text:
                final_text = text
            messages.append(
                cast(
                    MessageParam,
                    {"role": "assistant", "content": [b.model_dump() for b in message.content]},
                )
            )
            if message.stop_reason != "tool_use":
                break

            tool_results: list[ToolResultBlockParam] = []
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    result, is_error = self._workspace.dispatch(
                        block.name, cast(dict[str, Any], block.input)
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                            "is_error": is_error,
                        }
                    )
            messages.append(cast(MessageParam, {"role": "user", "content": tool_results}))

        usage = Usage(tokens_in=total_in, tokens_out=total_out)
        return self._response(model, final_text, usage)

    def _response(self, model: str, text: str, usage: Usage) -> AgentResponse:
        price = self._price_lookup(model)
        return AgentResponse(
            text=text,
            agent=self.name,
            model=model,
            usage=usage,
            cost_eur=price.estimate(usage.tokens_in, usage.tokens_out),
            bucket=self.bucket,
        )
