"""Evaluation harness: per-field accuracy of a classifier against labeled prompts.

Used to compare the fine-tuned model against the rule-router baseline on a held-out
test split, so we can see whether the learned Dirigent actually improves on (or at least
matches) the heuristics before switching the live strategy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cmn_ai.core import Classification
from cmn_ai.training.dataset import LabeledPrompt

ClassifyFn = Callable[[str], Classification]


@dataclass(frozen=True, slots=True)
class EvalResult:
    n: int
    capability_acc: float
    complexity_acc: float
    needs_web_acc: float
    exact_acc: float


def evaluate(samples: list[LabeledPrompt], classify_fn: ClassifyFn) -> EvalResult:
    """Score ``classify_fn`` over labeled samples, field by field."""
    n = len(samples)
    if n == 0:
        return EvalResult(0, 0.0, 0.0, 0.0, 0.0)

    cap_ok = comp_ok = web_ok = exact_ok = 0
    for sample in samples:
        predicted = classify_fn(sample.prompt)
        truth = sample.classification
        c = predicted.capability is truth.capability
        k = predicted.complexity is truth.complexity
        w = predicted.needs_web == truth.needs_web
        cap_ok += c
        comp_ok += k
        web_ok += w
        exact_ok += c and k and w

    return EvalResult(
        n=n,
        capability_acc=cap_ok / n,
        complexity_acc=comp_ok / n,
        needs_web_acc=web_ok / n,
        exact_acc=exact_ok / n,
    )
