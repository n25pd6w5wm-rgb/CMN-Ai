"""Dedicated cloud-coding agent (plan D-4).

Claude is used for code, with model tiering: Sonnet by default, escalating to Opus
for hard tasks (long prompts, code fences, architecture/refactor work). It always
draws on the isolated ``coding`` budget bucket so coding can never starve general use.
A coding-tuned system prompt is sent as a cached prefix to cut repeat input cost.

This is the Messages-API coding agent. The fuller agentic loop (Claude Agent SDK with
file/bash tools and a workspace) is the natural next step and slots in behind the same
``Agent`` interface — it is intentionally deferred until a live API key and a tool
sandbox design are in place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlock, TextBlockParam

from cmn_ai.agents.base import build_messages
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

    @property
    def active(self) -> bool:
        return bool(self.api_key)

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        model = self._hard_model if _looks_hard(task.prompt) else self.model
        client = AsyncAnthropic(api_key=self.api_key)
        messages = cast(list[MessageParam], build_messages(task))
        system_blocks: list[TextBlockParam] = [
            {
                "type": "text",
                "text": CODING_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        message = await client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            messages=messages,
            system=system_blocks,
        )

        text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
        usage = Usage(
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
        )
        price = self._price_lookup(model)
        return AgentResponse(
            text=text,
            agent=self.name,
            model=model,
            usage=usage,
            cost_eur=price.estimate(usage.tokens_in, usage.tokens_out),
            bucket=self.bucket,
        )
