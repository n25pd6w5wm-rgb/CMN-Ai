"""Anthropic (Claude) adapter using the official async SDK with prompt caching.

The system prompt is sent as a cache-marked block so its stable prefix is cached
across requests (``cache_control: ephemeral``), cutting input cost on repeat calls —
which matters directly to the budget governor. Model is injected by the factory
(Sonnet by default, Opus for hard tasks per the plan).
"""

from __future__ import annotations

from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlock, TextBlockParam

from cmn_ai.agents.base import build_messages
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage


class AnthropicAgent:
    """Adapter for Claude via the Messages API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        cost_per_mtok: CostPerMTok,
        capabilities: frozenset[Capability],
        bucket: Bucket,
        max_tokens: int = 8192,
    ) -> None:
        self.name = "anthropic"
        self.api_key = api_key
        self.model = model
        self.cost_per_mtok = cost_per_mtok
        self.capabilities = capabilities
        self.bucket = bucket
        self._max_tokens = max_tokens

    @property
    def active(self) -> bool:
        return bool(self.api_key)

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        client = AsyncAnthropic(api_key=self.api_key)
        messages = cast(list[MessageParam], build_messages(task))

        if system is not None:
            # Mark the stable system prefix for prompt caching.
            system_blocks: list[TextBlockParam] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            message = await client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=messages,
                system=system_blocks,
            )
        else:
            message = await client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=messages,
            )

        text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
        usage = Usage(
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
        )
        return AgentResponse(
            text=text,
            agent=self.name,
            model=self.model,
            usage=usage,
            cost_eur=self.cost_per_mtok.estimate(usage.tokens_in, usage.tokens_out),
            bucket=self.bucket,
        )
