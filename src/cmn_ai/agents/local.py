"""Local Ollama agent — runs Gemma (or any local model) for free.

This is the workhorse that carries the bulk of traffic at zero cost, per the plan's
core idea: a free local model handles volume, paid APIs are used only on purpose.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from cmn_ai.agents.base import build_messages
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage

_FREE = CostPerMTok(input_eur=0.0, output_eur=0.0)
_CAPABILITIES = frozenset({Capability.CHAT, Capability.CODE})
_HEALTH_TTL_SECONDS = 30.0
_HEALTH_TIMEOUT_SECONDS = 2.0


class OllamaAgent:
    """Adapter for a model served locally by Ollama via its ``/api/chat`` endpoint."""

    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout: float = 120.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = "local"
        self.host = host.rstrip("/")
        self.model = model
        self.capabilities = _CAPABILITIES
        self.cost_per_mtok = _FREE
        self.bucket = Bucket.GENERAL
        self._timeout = timeout
        # Pessimistic start: an unreachable Pi must never receive the first request.
        # refresh_health() flips this based on an actual reachability probe.
        self._healthy = False
        self._checked_at: float | None = None
        self._now = now

    @property
    def active(self) -> bool:
        return self._healthy

    async def refresh_health(self) -> None:
        """Probe Ollama's ``/api/tags`` and cache the result for a short TTL.

        Both outcomes are cached so a dead Pi costs at most one 2s probe per TTL
        window instead of a doomed 120s chat call per request.
        """
        now = self._now()
        if self._checked_at is not None and now - self._checked_at < _HEALTH_TTL_SECONDS:
            return
        self._checked_at = now
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_SECONDS) as client:
                resp = await client.get(f"{self.host}/api/tags")
            self._healthy = resp.status_code == 200
        except Exception:
            self._healthy = False

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
