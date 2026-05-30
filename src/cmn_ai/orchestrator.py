"""Orchestrator — the per-request glue tying router, agents and governor together.

Flow per request (see plan's data flow): classify (free, local) -> select an agent
under the budget -> optionally optimise the prompt -> run the agent -> book the actual
spend in the ledger -> return the routing decision plus the response. A blocked
decision short-circuits: no agent runs and nothing is billed.
"""

from __future__ import annotations

from collections.abc import Mapping

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.core import AgentResponse, RouteDecision, Task
from cmn_ai.router.interface import Router

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
    ) -> None:
        self._agents = agents
        self._router = router
        self._governor = governor

    def route(self, task: Task) -> RouteDecision:
        """Classify and select without running anything (used by route-debug)."""
        classification = self._router.classify(task)
        return self._router.select(task, classification, self._agents, self._governor)

    async def handle(self, task: Task) -> tuple[RouteDecision, AgentResponse | None]:
        """Route the task, run the chosen agent, and book its spend."""
        decision = self.route(task)
        if decision.blocked:
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
        return decision, response
