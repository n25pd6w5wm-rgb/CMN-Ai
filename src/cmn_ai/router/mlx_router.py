"""MLXRouter — the learned Dirigent: a fine-tuned model does classification.

Only ``classify`` is learned. The model is asked to emit a JSON classification; on any
failure (model error, unparseable output) it falls back to the wrapped ``RuleRouter``.
Everything money-related — ``select`` (agent choice under budget) — and the prompt help
(``optimize_prompt``/``synthesize``) are delegated unchanged to the RuleRouter, so the
deterministic budget policy is never replaced by a model. Generation is injected as a
``generate_fn`` so this class is fully unit-testable without MLX.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.core import AgentResponse, Classification, RouteDecision, Task
from cmn_ai.router.rule_router import RuleRouter
from cmn_ai.training.prompts import parse_classification

GenerateFn = Callable[[str], str]


class MLXRouter:
    """Router whose classification comes from a fine-tuned model, rules as fallback."""

    def __init__(self, *, rule_router: RuleRouter, generate_fn: GenerateFn) -> None:
        self._rule = rule_router
        self._generate = generate_fn

    def classify(self, task: Task) -> Classification:
        try:
            output = self._generate(task.prompt)
        except Exception:
            return self._rule.classify(task)
        parsed = parse_classification(output)
        if parsed is not None:
            return parsed
        return self._rule.classify(task)

    def select(
        self,
        task: Task,
        classification: Classification,
        agents: Mapping[str, Agent],
        governor: BudgetGovernor,
    ) -> RouteDecision:
        return self._rule.select(task, classification, agents, governor)

    async def optimize_prompt(self, task: Task) -> Task:
        return await self._rule.optimize_prompt(task)

    async def synthesize(self, task: Task, responses: list[AgentResponse]) -> str:
        return await self._rule.synthesize(task, responses)
