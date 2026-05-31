"""Tests for the shared routing prompt template + classification (de)serialization."""

from __future__ import annotations

from cmn_ai.core import Bucket, Capability, Classification, Complexity
from cmn_ai.training.prompts import (
    SYSTEM_INSTRUCTION,
    build_classify_messages,
    classification_to_json,
    parse_classification,
)


def _c(
    cap: Capability,
    comp: Complexity = Complexity.LOW,
    web: bool = False,
) -> Classification:
    bucket = Bucket.CODING if cap is Capability.CODE else Bucket.GENERAL
    return Classification(capability=cap, complexity=comp, needs_web=web, bucket=bucket)


def test_serialize_then_parse_round_trips() -> None:
    original = _c(Capability.CODE, Complexity.HIGH, web=False)
    text = classification_to_json(original)
    parsed = parse_classification(text)
    assert parsed == original


def test_bucket_is_derived_from_capability_on_parse() -> None:
    parsed = parse_classification('{"capability": "code", "complexity": "low", "needs_web": false}')
    assert parsed is not None
    assert parsed.bucket is Bucket.CODING
    other = parse_classification('{"capability": "chat", "complexity": "low", "needs_web": false}')
    assert other is not None
    assert other.bucket is Bucket.GENERAL


def test_parse_tolerates_surrounding_text() -> None:
    raw = (
        "Sure! Here is the classification:\n"
        '{"capability":"research","complexity":"low","needs_web":true} \nDone.'
    )
    parsed = parse_classification(raw)
    assert parsed is not None
    assert parsed.capability is Capability.RESEARCH
    assert parsed.needs_web is True


def test_parse_returns_none_on_garbage() -> None:
    assert parse_classification("not json at all") is None
    assert parse_classification("") is None


def test_parse_returns_none_on_invalid_enum_value() -> None:
    assert (
        parse_classification('{"capability": "banana", "complexity": "low", "needs_web": false}')
        is None
    )


def test_parse_returns_none_on_missing_field() -> None:
    assert parse_classification('{"capability": "chat", "complexity": "low"}') is None


def test_build_classify_messages_has_system_and_user() -> None:
    messages = build_classify_messages("write a python function")
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_INSTRUCTION
    assert messages[-1] == {"role": "user", "content": "write a python function"}
