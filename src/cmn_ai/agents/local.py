"""Local Ollama agent — runs Gemma (or any local model) for free.

This is the workhorse that carries the bulk of traffic at zero cost, per the plan's
core idea: a free local model handles volume, paid APIs are used only on purpose.
"""

from __future__ import annotations

import httpx

from cmn_ai.agents.base import build_messages
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage

_FREE = CostPerMTok(input_eur=0.0, output_eur=0.0)
_CAPABILITIES = frozenset({Capability.CHAT, Capability.CODE})


class OllamaAgent:
    """Adapter for a model served locally by Ollama via its ``/api/chat`` endpoint."""

    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        self.name = "local"
        self.host = host.rstrip("/")
        self.model = model
        self.capabilities = _CAPABILITIES
        self.cost_per_mtok = _FREE
        self.bucket = Bucket.GENERAL
        self.active = True
        self._timeout = timeout

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        messages = build_messages(task)
        if system is not None:
            messages.insert(0, {"role": "system", "content": system})

        payload = {"model": self.model, "messages": messages, "stream": False}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self.host}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        usage = Usage(
            tokens_in=int(data.get("prompt_eval_count", 0)),
            tokens_out=int(data.get("eval_count", 0)),
        )
        return AgentResponse(
            text=data["message"]["content"],
            agent=self.name,
            model=self.model,
            usage=usage,
            cost_eur=0.0,
            bucket=self.bucket,
        )
