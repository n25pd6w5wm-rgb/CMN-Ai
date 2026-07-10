"""Tests for the model pricing table (single source of truth for cost)."""

from __future__ import annotations

import pytest

from cmn_ai.budget.pricing import price_for
from cmn_ai.core import CostPerMTok


def test_known_model_returns_price() -> None:
    price = price_for("claude-sonnet-4-6")
    assert isinstance(price, CostPerMTok)
    assert price.input_eur > 0
    assert price.output_eur > price.input_eur


def test_local_model_is_free() -> None:
    assert price_for("gemma4:latest") == CostPerMTok(0.0, 0.0)


def test_any_local_gemma_tag_is_free() -> None:
    # The local agent picks whichever Gemma the Ollama host has pulled, so every
    # gemma3/gemma4 tag must price as free without being enumerated one by one.
    for model in ("gemma3:12b", "gemma4:e2b-it-qat", "gemma4:26b"):
        assert price_for(model) == CostPerMTok(0.0, 0.0)


def test_unknown_model_raises() -> None:
    with pytest.raises(KeyError):
        price_for("totally-made-up-model")


def test_unknown_paid_gemma_lookalike_still_raises() -> None:
    # Only ollama-style local tags are free; a paid API id must never zero-bill.
    with pytest.raises(KeyError):
        price_for("gemma-4-api-preview")
