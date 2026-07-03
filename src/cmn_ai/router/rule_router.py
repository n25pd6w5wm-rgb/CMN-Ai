"""RuleRouter — the heuristic Dirigent (classifier + policy + prompt help).

Classification is keyword/length/code-fence based and runs locally for free. The
policy keeps the plan's core economics: the free local model carries low-complexity
volume, while paid agents are engaged only when the task needs them *and* the budget
allows. When a needed paid agent is unaffordable, behaviour follows the configured
``on_limit`` policy (hard block by default; optional free fallback).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace

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
    # German (users write German prompts; keywords are matched lowercased)
    "programmier",
    "quelltext",
    "skript",
    "fehlermeldung",
    "algorithmus",
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
    # German
    "neueste",
    "aktuell",
    "nachrichten",
    "heute",
    "wetter",
    "preis von",
)
_MULTIMODAL_KEYWORDS = ("image", "photo", "picture", "screenshot", "diagram", "foto")
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
    # German
    "architektur",
    "analysier",
    "beweis",
    "optimier",
    "komplex",
    "verteilte",
    "konzept",
    "strategie",
)
_HARD_LENGTH = 400
_FREE = CostPerMTok(0.0, 0.0)
_DEFAULT_OUTPUT_TOKENS = 1500

# Paid-agent preference by task shape (agent names in priority order). Agents not
# listed follow, sorted by output price. Low-complexity chat stays cheapest-first —
# that is the volume traffic the budget lives on.
_HIGH_CHAT_PREFERENCE = ("anthropic", "openai")
_CODE_PREFERENCE = ("anthropic", "openai")  # after coding-bucket agents
_MULTIMODAL_PREFERENCE = ("gemini", "anthropic")

# Prompt optimisation (optional, local). Only substantial prompts are worth rewriting;
# trivial ones rarely benefit and risk distortion. A rewrite is rejected if it is empty
# or runs away in length, so optimisation can only help, never break, a request.
_OPTIMIZE_MIN_CHARS = 80
_OPTIMIZE_SYSTEM = (
    "You rewrite a user's request so another AI can answer it better. Make it clearer, "
    "more specific and well-structured while preserving the user's exact intent, language "
    "and every constraint. Do NOT answer it, add new facts, or ask questions. Output only "
    "the rewritten request, with no preamble or explanation."
)


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

        # Multimodal keywords match whole words only — "photo" must not fire on
        # "Photosynthese". The other keyword groups keep substring matching on
        # purpose (they include stems like "programmier" and "optimier").
        has_mm_keyword = any(re.search(rf"\b{re.escape(k)}\b", lower) for k in _MULTIMODAL_KEYWORDS)
        if task.images or has_mm_keyword:
            capability = Capability.MULTIMODAL
        elif has_fence or any(k in lower for k in _CODE_KEYWORDS):
            capability = Capability.CODE
        elif any(k in lower for k in _RESEARCH_KEYWORDS):
            capability = Capability.RESEARCH
        else:
            capability = Capability.CHAT

        # A dropped document (text attachment) deserves a strong model even when the
        # visible prompt is short — the real work sits in the extracted file content.
        has_document = task.has_attachments and not task.images
        complexity = (
            Complexity.HIGH
            if has_fence
            or has_document
            or len(prompt) > _HARD_LENGTH
            or any(k in lower for k in _HARD_KEYWORDS)
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

        ranked = self._paid_preference(paid, classification)
        for candidate in ranked:
            est = _estimate_eur(candidate, task)
            if governor.can_spend(candidate.bucket, est):
                return self._decide(classification, candidate, "best paid agent within budget", est)
        if ranked:
            if self._on_limit is OnLimit.FALLBACK_FREE and free:
                return self._decide(
                    classification,
                    free[0],
                    "budget exhausted — falling back to free local model",
                    0.0,
                    fell_back=True,
                )
            top = ranked[0]
            return self._blocked(classification, top, _estimate_eur(top, task))

        if free:
            return self._decide(
                classification,
                free[0],
                "no suitable paid agent available — using free local model",
                0.0,
                fell_back=True,
            )
        return self._blocked(classification, None, 0.0)

    def _paid_preference(self, paid: list[Agent], c: Classification) -> list[Agent]:
        """Rank paid agents for this task: preferred names first, then by price.

        High-complexity chat deserves a strong model (Sonnet-tier) rather than the
        globally cheapest; code goes to the coding-bucket specialist; multimodal to a
        vision-capable model. Everything unlisted trails in cheapest-output order, so
        the first *affordable* entry of the ranking wins.
        """
        if not paid:
            return []
        by_cost = sorted(paid, key=lambda a: (a.cost_per_mtok.output_eur, a.name))
        named = {a.name: a for a in paid}

        preferred: list[Agent] = []
        if c.capability is Capability.CODE:
            preferred = [a for a in by_cost if a.bucket is Bucket.CODING]
            preferred += [named[n] for n in _CODE_PREFERENCE if n in named]
        elif c.capability is Capability.MULTIMODAL:
            preferred = [named[n] for n in _MULTIMODAL_PREFERENCE if n in named]
        elif c.capability is Capability.CHAT and c.complexity is Complexity.HIGH:
            preferred = [named[n] for n in _HIGH_CHAT_PREFERENCE if n in named]

        ranked = list(dict.fromkeys(preferred))  # de-dupe, keep order
        ranked += [a for a in by_cost if a not in ranked]
        return ranked

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
        reason = (
            "weekly budget for this category is exhausted"
            if agent is not None
            else "no agent available for this task — is the local model reachable "
            "or an API key configured?"
        )
        return RouteDecision(
            classification=c,
            agent=agent.name if agent else "",
            model=agent.model if agent else "",
            reason=reason,
            estimated_eur=est,
            blocked=True,
        )

    # -- prompt help (optional, local) -------------------------------------

    async def optimize_prompt(self, task: Task) -> Task:
        """Optionally rewrite the prompt via a local model (e.g. Gemma).

        Conservative by design: runs only when an optimizer agent is configured and the
        prompt is substantial enough to benefit — never for trivial prompts or ones with
        attachments. Any failure or degenerate rewrite (empty or runaway) falls back to
        the original task untouched. History and attachments are always preserved.
        """
        if self._optimizer is None or task.has_attachments:
            return task
        original = task.prompt
        if len(original.strip()) < _OPTIMIZE_MIN_CHARS:
            return task
        try:
            response = await self._optimizer.run(Task(prompt=original), system=_OPTIMIZE_SYSTEM)
        except Exception as exc:  # never let optimisation break a request
            print(f"[cmn-ai] prompt optimisation failed ({exc}); using original prompt.")
            return task
        rewritten = response.text.strip()
        if not rewritten or len(rewritten) > len(original) * 4 + 200:
            return task
        return replace(task, prompt=rewritten)

    async def synthesize(self, task: Task, responses: list[AgentResponse]) -> str:
        """Combine responses. With a single response this is a pass-through."""
        if not responses:
            return ""
        if len(responses) == 1:
            return responses[0].text
        return "\n\n---\n\n".join(r.text for r in responses)
