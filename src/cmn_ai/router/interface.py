"""The Router protocol — the whole Dirigent is one swappable strategy.

``RuleRouter`` is today's implementation; a future ``TrainedRouter`` (a small
fine-tuned model) can replace it wholesale as long as it satisfies this protocol.
That is the central modularity requirement of the plan: the classifier/policy is
decoupled from the rest of the system so it can be retrained and swapped via config.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.core import AgentResponse, Classification, RouteDecision, Task


@runtime_checkable
class Router(Protocol):
    """Strategy for classifying a task and routing it to an agent."""

    def classify(self, task: Task) -> Classification:
        """Read what the task needs (capability, complexity, web, bucket). Local/free."""
        ...

    def select(
        self,
        task: Task,
        classification: Classification,
        agents: Mapping[str, Agent],
        governor: BudgetGovernor,
    ) -> RouteDecision:
        """Choose an agent under the budget, returning a transparent decision."""
        ...

    async def optimize_prompt(self, task: Task) -> Task:
        """Optionally rewrite the prompt (e.g. via local Gemma). May be a no-op."""
        ...

    async def synthesize(self, task: Task, responses: list[AgentResponse]) -> str:
        """Combine one or more agent responses into a final answer."""
        ...
