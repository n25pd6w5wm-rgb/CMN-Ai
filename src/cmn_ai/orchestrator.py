"""Orchestrator — the per-request glue tying router, agents and governor together.

Flow per request (see plan's data flow): classify (free, local) -> select an agent
under the budget -> optionally optimise the prompt -> run the agent -> book the actual
spend in the ledger -> return the routing decision plus the response. A blocked
decision short-circuits: no agent runs and nothing is billed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.core import AgentResponse, RouteDecision, Task
from cmn_ai.router.interface import Router
from cmn_ai.storage.decisions import DecisionLog


def _utc_now() -> datetime:
    return datetime.now(UTC)


SYSTEM_PROMPT = (
    "You are cmn-ai, a helpful, concise assistant. Answer directly and accurately. "
    "If you are unsure, say so rather than inventing facts."
)


class Orchestrator:
    """Coordinates a single chat request end to end."""

    def __init__(
        self,
        *,
        agents: Mapping[str, Agent],
        router: Router,
        governor: BudgetGovernor,
        decision_log: DecisionLog | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._agents = agents
        self._router = router
        self._governor = governor
        self._decision_log = decision_log
        self._now = now

    def route(self, task: Task, *, agent_override: str | None = None) -> RouteDecision:
        """Classify and select without running anything (used by route-debug).

        If ``agent_override`` names an active agent, the user picked it explicitly — use
        it (still subject to the budget) instead of letting the router choose.
        """
        classification = self._router.classify(task)
        if agent_override and agent_override in self._agents:
            agent = self._agents[agent_override]
            if agent.active:
                cost = agent.cost_per_mtok
                is_free = cost.input_eur == 0 and cost.output_eur == 0
                est = 0.0 if is_free else cost.estimate(max(1, len(task.prompt) // 4), 800)
                if is_free or self._governor.can_spend(agent.bucket, est):
                    return RouteDecision(
                        classification=classification,
                        agent=agent.name,
                        model=agent.model,
                        reason=f"user-selected {agent.name}",
                        estimated_eur=est,
                    )
                return RouteDecision(
                    classification=classification,
                    agent=agent.name,
                    model=agent.model,
                    reason="weekly budget for this category is exhausted",
                    estimated_eur=est,
                    blocked=True,
                )
        return self._router.select(task, classification, self._agents, self._governor)

    async def handle(
        self, task: Task, *, agent_override: str | None = None
    ) -> tuple[RouteDecision, AgentResponse | None]:
        """Route the task, run the chosen agent, and book its spend."""
        decision = self.route(task, agent_override=agent_override)
        if decision.blocked:
            self._log(task.prompt, decision, None)
            return decision, None

        task = await self._router.optimize_prompt(task)
        agent = self._agents[decision.agent]
        response = await agent.run(task, system=SYSTEM_PROMPT)
        self._governor.record(
            response.bucket,
            response.agent,
            response.model,
            response.usage,
            eur=response.cost_eur,
        )
        self._log(task.prompt, decision, response)
        return decision, response

    def _log(self, prompt: str, decision: RouteDecision, response: AgentResponse | None) -> None:
        if self._decision_log is not None:
            self._decision_log.log(prompt, decision, response, at=self._now())
