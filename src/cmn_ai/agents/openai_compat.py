"""OpenAI-compatible chat adapter.

A single implementation serves any provider that speaks the OpenAI
``/chat/completions`` shape — currently OpenAI itself and Perplexity (whose Sonar
models use the same wire format). The provider is distinguished only by ``name``,
``base_url``, ``model``, price and capabilities, all injected by the factory.
"""

from __future__ import annotations

import httpx

from cmn_ai.agents.base import build_messages
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage


class OpenAICompatibleAgent:
    """Adapter for OpenAI-style ``POST {base_url}/chat/completions`` endpoints."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        model: str,
        cost_per_mtok: CostPerMTok,
        capabilities: frozenset[Capability],
        bucket: Bucket,
        timeout: float = 120.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.cost_per_mtok = cost_per_mtok
        self.capabilities = capabilities
        self.bucket = bucket
        self._timeout = timeout

    @property
    def active(self) -> bool:
        """A cloud agent is only usable once its API key is configured."""
        return bool(self.api_key)

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        messages = build_messages(task)
        if system is not None:
            messages.insert(0, {"role": "system", "content": system})

        payload = {"model": self.model, "messages": messages}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()

        raw_usage = data.get("usage") or {}
        usage = Usage(
            tokens_in=int(raw_usage.get("prompt_tokens", 0)),
            tokens_out=int(raw_usage.get("completion_tokens", 0)),
        )
        return AgentResponse(
            text=data["choices"][0]["message"]["content"],
            agent=self.name,
            model=self.model,
            usage=usage,
            cost_eur=self.cost_per_mtok.estimate(usage.tokens_in, usage.tokens_out),
            bucket=self.bucket,
        )
