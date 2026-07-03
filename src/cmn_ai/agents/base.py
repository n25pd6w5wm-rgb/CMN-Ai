"""The Agent protocol and shared helpers.

Every model integration is an ``Agent``: it advertises a ``name``, the model it
drives, what it ``capabilities`` it can serve, its price (``cost_per_mtok``), which
budget ``bucket`` it draws from, and whether it is ``active`` (configured + usable).
Adding a new AI means adding a new file implementing this protocol and registering it
in the factory — no change to the core is needed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Message, Task


@runtime_checkable
class Agent(Protocol):
    """Structural contract for a model adapter."""

    name: str
    model: str
    capabilities: frozenset[Capability]
    cost_per_mtok: CostPerMTok
    bucket: Bucket

    @property
    def active(self) -> bool:
        """Whether the agent is configured and usable (e.g. has its API key)."""
        ...

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        """Execute the task and return the response plus its measured cost."""
        ...


@runtime_checkable
class HealthCheckedAgent(Protocol):
    """Agents whose ``active`` state must be discovered by probing a backend.

    Cloud adapters derive ``active`` from the presence of an API key; the local
    Ollama agent instead exposes ``refresh_health()`` so the orchestrator can skip
    an unreachable Pi up front rather than failing into it on every request.
    """

    async def refresh_health(self) -> None:
        """Re-probe the backend (implementations should cache with a short TTL)."""
        ...


def build_messages(task: Task) -> list[dict[str, str]]:
    """Turn a Task (history + prompt) into OpenAI/Ollama-style chat messages.

    The system prompt is handled separately by each adapter, so it is not included
    here. The new user prompt is appended after the prior turns.
    """
    messages = [{"role": m.role, "content": m.content} for m in task.history]
    messages.append({"role": "user", "content": task.prompt})
    return messages


def history_as_messages(history: tuple[Message, ...]) -> list[dict[str, str]]:
    """Convert only prior history to chat-message dicts (no new prompt appended)."""
    return [{"role": m.role, "content": m.content} for m in history]
