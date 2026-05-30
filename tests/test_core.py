"""Tests for the core domain types."""

from __future__ import annotations

from cmn_ai.core import Bucket, Capability, CostPerMTok, Usage


def test_cost_per_mtok_estimate() -> None:
    price = CostPerMTok(input_eur=3.0, output_eur=15.0)
    # 1M in + 1M out = 3 + 15
    assert price.estimate(1_000_000, 1_000_000) == 18.0
    # half a million out only
    assert price.estimate(0, 500_000) == 7.5


def test_usage_adds() -> None:
    total = Usage(10, 20) + Usage(5, 7)
    assert total == Usage(15, 27)


def test_enums_are_strings() -> None:
    assert Capability.CODE.value == "code"
    assert Bucket.CODING.value == "coding"
    assert Capability("code") is Capability.CODE
