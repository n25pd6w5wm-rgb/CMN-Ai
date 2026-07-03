"""Core domain types — the contract shared by router, agents, budget and web.

These types are intentionally dependency-free (stdlib dataclasses + enums) so that
every layer can depend on them without creating import cycles. Anything that crosses
a module boundary (a request, a routing decision, a model response, a cost) is defined
here as a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    """What kind of work a task needs — also what an agent can provide."""

    CHAT = "chat"
    CODE = "code"
    RESEARCH = "research"
    MULTIMODAL = "multimodal"


class Complexity(StrEnum):
    """How hard a task is, used to pick a cheap vs. a strong model."""

    LOW = "low"
    HIGH = "high"


class Bucket(StrEnum):
    """Budget category. Coding has its own bucket so it never starves general use."""

    GENERAL = "general"
    CODING = "coding"


@dataclass(frozen=True, slots=True)
class Message:
    """A single turn in a conversation."""

    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class Task:
    """A raw request entering the system, before classification."""

    prompt: str
    history: tuple[Message, ...] = ()
    has_attachments: bool = False
    # Uploaded images as (mime_type, base64) pairs — routed to a vision-capable model.
    images: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Classification:
    """The router's read of a task: what it needs and how hard it is."""

    capability: Capability
    complexity: Complexity
    needs_web: bool
    bucket: Bucket


@dataclass(frozen=True, slots=True)
class CostPerMTok:
    """Price of a model in EUR per million tokens, split by direction."""

    input_eur: float
    output_eur: float

    def estimate(self, tokens_in: int, tokens_out: int) -> float:
        """EUR cost for a given token count."""
        return tokens_in / 1_000_000 * self.input_eur + tokens_out / 1_000_000 * self.output_eur


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts reported by (or estimated for) a single model call."""

    tokens_in: int = 0
    tokens_out: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
        )


@dataclass(frozen=True, slots=True)
class AgentResponse:
    """The result of running one agent: the text plus what it cost."""

    text: str
    agent: str
    model: str
    usage: Usage = field(default_factory=Usage)
    cost_eur: float = 0.0
    bucket: Bucket = Bucket.GENERAL
    # Model reasoning (thought summaries), shown collapsed in the UI. Ephemeral —
    # streamed to the client but not persisted with the conversation.
    thinking: str = ""


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """A transparent record of how a task was routed — also a training sample."""

    classification: Classification
    agent: str
    model: str
    reason: str
    estimated_eur: float
    fell_back: bool = False
    blocked: bool = False
