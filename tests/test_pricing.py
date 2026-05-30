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


def test_unknown_model_raises() -> None:
    with pytest.raises(KeyError):
        price_for("totally-made-up-model")
