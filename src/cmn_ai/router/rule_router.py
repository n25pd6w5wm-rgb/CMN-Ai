"""RuleRouter — the heuristic Dirigent (classifier + policy + prompt help).

Classification is keyword/length/code-fence based and runs locally for free. The
policy keeps the plan's core economics: the free local model carries low-complexity
volume, while paid agents are engaged only when the task needs them *and* the budget
allows. When a needed paid agent is unaffordable, behaviour follows the configured
``on_limit`` policy (hard block by default; optional free fallback).
"""

from __future__ import annotations

from collections.abc import Mapping

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.config import OnLimit
from cmn_ai.core import (
    AgentResponse,
    Bucket,
    Capability,
    Classification,
    Complexity,
    CostPerMTok,
    RouteDecision,
    Task,
)

_CODE_KEYWORDS = (
    "function",
    "code",
    "bug",
    "refactor",
    "python",
    "javascript",
    "typescript",
    "rust",
    "golang",
    " java",
    "compile",
    "stack trace",
    "stacktrace",
    "traceback",
    "algorithm",
    "regex",
    "sql",
    "def ",
    "class ",
)
_RESEARCH_KEYWORDS = (
    "latest",
    "news",
    "current",
    "today",
    "recent",
    "look up",
    "search for",
    "who won",
    "price of",
    "weather",
    "this year",
    "right now",
    "2025",
    "2026",
)
_MULTIMODAL_KEYWORDS = ("image", "photo", "picture", "screenshot", "diagram")
_HARD_KEYWORDS = (
    "architecture",
    "design",
    "prove",
    "optimize",
    "complex",
    "refactor",
    "debug",
    "distributed",
    "proof",
)
_HARD_LENGTH = 400
_FREE = CostPerMTok(0.0, 0.0)
_DEFAULT_OUTPUT_TOKENS = 800


def _is_free(agent: Agent) -> bool:
    return agent.cost_per_mtok == _FREE


def _estimate_eur(agent: Agent, task: Task) -> float:
    est_in = max(1, len(task.prompt) // 4)
    return agent.cost_per_mtok.estimate(est_in, _DEFAULT_OUTPUT_TOKENS)


class RuleRouter:
    """Heuristic implementation of the Router protocol."""

    def __init__(
        self,
        *,
        on_limit: OnLimit = OnLimit.BLOCK,
        optimizer: Agent | None = None,
    ) -> None:
        self._on_limit = on_limit
        self._optimizer = optimizer

    # -- classification ----------------------------------------------------

    def classify(self, task: Task) -> Classification:
        prompt = task.prompt
        lower = prompt.lower()
        has_fence = "```" in prompt

        if task.has_attachments or any(k in lower for k in _MULTIMODAL_KEYWORDS):
            capability = Capability.MULTIMODAL
        elif has_fence or any(k in lower for k in _CODE_KEYWORDS):
            capability = Capability.CODE
        elif any(k in lower for k in _RESEARCH_KEYWORDS):
            capability = Capability.RESEARCH
        else:
            capability = Capability.CHAT

        complexity = (
            Complexity.HIGH
            if has_fence or len(prompt) > _HARD_LENGTH or any(k in lower for k in _HARD_KEYWORDS)
            else Complexity.LOW
        )
        bucket = Bucket.CODING if capability is Capability.CODE else Bucket.GENERAL
        needs_web = capability is Capability.RESEARCH
        return Classification(
            capability=capability,
            complexity=complexity,
            needs_web=needs_web,
            bucket=bucket,
        )

    # -- agent selection ---------------------------------------------------

    def select(
        self,
        task: Task,
        classification: Classification,
        agents: Mapping[str, Agent],
        governor: BudgetGovernor,
    ) -> RouteDecision:
        capable = [
            a for a in agents.values() if a.active and classification.capability in a.capabilities
        ]
        free = [a for a in capable if _is_free(a)]
        paid = [a for a in capable if not _is_free(a)]

        prefer_free = (
            classification.capability in {Capability.CHAT, Capability.CODE}
            and classification.complexity is Complexity.LOW
        )
        if prefer_free and free:
            return self._decide(
                classification, free[0], "free local model for low-complexity task", 0.0
            )

        candidate = self._pick_paid(paid, classification)
        if candidate is not None:
            est = _estimate_eur(candidate, task)
            if governor.can_spend(candidate.bucket, est):
                return self._decide(classification, candidate, "best paid agent within budget", est)
            if self._on_limit is OnLimit.FALLBACK_FREE and free:
                return self._decide(
                    classification,
                    free[0],
                    "budget exhausted — falling back to free local model",
                    0.0,
                    fell_back=True,
                )
            return self._blocked(classification, candidate, est)

        if free:
            return self._decide(
                classification,
                free[0],
                "no suitable paid agent available — using free local model",
                0.0,
                fell_back=True,
            )
        return self._blocked(classification, None, 0.0)

    def _pick_paid(self, paid: list[Agent], c: Classification) -> Agent | None:
        if not paid:
            return None
        pool = paid
        if c.capability is Capability.CODE:
            coding = [a for a in paid if a.bucket is Bucket.CODING]
            if coding:
                pool = coding
        return min(pool, key=lambda a: (a.cost_per_mtok.output_eur, a.name))

    def _decide(
        self,
        c: Classification,
        agent: Agent,
        reason: str,
        est: float,
        *,
        fell_back: bool = False,
    ) -> RouteDecision:
        return RouteDecision(
            classification=c,
            agent=agent.name,
            model=agent.model,
            reason=reason,
            estimated_eur=est,
            fell_back=fell_back,
        )

    def _blocked(self, c: Classification, agent: Agent | None, est: float) -> RouteDecision:
        return RouteDecision(
            classification=c,
            agent=agent.name if agent else "",
            model=agent.model if agent else "",
            reason="weekly budget for this category is exhausted",
            estimated_eur=est,
            blocked=True,
        )

    # -- prompt help (optional, local) -------------------------------------

    async def optimize_prompt(self, task: Task) -> Task:
        """No-op for now; a hook for local prompt rewriting in a later phase."""
        return task

    async def synthesize(self, task: Task, responses: list[AgentResponse]) -> str:
        """Combine responses. With a single response this is a pass-through."""
        if not responses:
            return ""
        if len(responses) == 1:
            return responses[0].text
        return "\n\n---\n\n".join(r.text for r in responses)
