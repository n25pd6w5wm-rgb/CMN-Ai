"""Tests for the evaluation harness (per-field accuracy)."""

from __future__ import annotations

from cmn_ai.core import Bucket, Capability, Classification, Complexity
from cmn_ai.training.dataset import LabeledPrompt
from cmn_ai.training.evaluate import evaluate


def _c(cap: Capability, comp: Complexity, web: bool) -> Classification:
    return Classification(
        capability=cap,
        complexity=comp,
        needs_web=web,
        bucket=Bucket.CODING if cap is Capability.CODE else Bucket.GENERAL,
    )


_SAMPLES = [
    LabeledPrompt("a", _c(Capability.CHAT, Complexity.LOW, False)),
    LabeledPrompt("b", _c(Capability.CODE, Complexity.HIGH, False)),
    LabeledPrompt("c", _c(Capability.RESEARCH, Complexity.LOW, True)),
    LabeledPrompt("d", _c(Capability.MULTIMODAL, Complexity.LOW, False)),
]
_TRUTH = {s.prompt: s.classification for s in _SAMPLES}


def test_perfect_classifier_scores_one() -> None:
    result = evaluate(_SAMPLES, lambda p: _TRUTH[p])
    assert result.n == 4
    assert result.capability_acc == 1.0
    assert result.complexity_acc == 1.0
    assert result.needs_web_acc == 1.0
    assert result.exact_acc == 1.0


def test_complexity_errors_lower_only_that_field() -> None:
    def classify(p: str) -> Classification:
        truth = _TRUTH[p]
        # always predict LOW complexity (wrong for "b")
        return Classification(
            capability=truth.capability,
            complexity=Complexity.LOW,
            needs_web=truth.needs_web,
            bucket=truth.bucket,
        )

    result = evaluate(_SAMPLES, classify)
    assert result.capability_acc == 1.0
    assert result.needs_web_acc == 1.0
    assert result.complexity_acc == 0.75  # 3/4 correct
    assert result.exact_acc == 0.75  # the one complexity miss fails exact too


def test_empty_samples_are_zero() -> None:
    result = evaluate([], lambda p: _c(Capability.CHAT, Complexity.LOW, False))
    assert result.n == 0
    assert result.exact_acc == 0.0
