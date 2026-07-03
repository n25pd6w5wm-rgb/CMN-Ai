"""Tests for RuleRouter.classify — the free local task classifier."""

from __future__ import annotations

from cmn_ai.core import Bucket, Capability, Complexity, Task
from cmn_ai.router.rule_router import RuleRouter


def _classify(prompt: str, **kw: object) -> object:
    return RuleRouter().classify(Task(prompt=prompt, **kw))  # type: ignore[arg-type]


def test_plain_chat_is_chat_low_general() -> None:
    c = RuleRouter().classify(Task(prompt="hello, how are you?"))
    assert c.capability is Capability.CHAT
    assert c.complexity is Complexity.LOW
    assert c.needs_web is False
    assert c.bucket is Bucket.GENERAL


def test_code_fence_is_code_and_coding_bucket() -> None:
    c = RuleRouter().classify(Task(prompt="fix this:\n```py\nprint(x)\n```"))
    assert c.capability is Capability.CODE
    assert c.bucket is Bucket.CODING


def test_code_keywords_detected() -> None:
    c = RuleRouter().classify(Task(prompt="Write a Python function to reverse a list"))
    assert c.capability is Capability.CODE


def test_research_needs_web() -> None:
    c = RuleRouter().classify(Task(prompt="What is the latest news about the election?"))
    assert c.capability is Capability.RESEARCH
    assert c.needs_web is True


def test_images_classify_multimodal() -> None:
    c = RuleRouter().classify(
        Task(prompt="was ist da zu sehen?", has_attachments=True, images=(("image/png", "eA=="),))
    )
    assert c.capability is Capability.MULTIMODAL


def test_text_attachments_bump_complexity_not_multimodal() -> None:
    # a dropped document deserves a strong text model, not a vision detour
    c = RuleRouter().classify(Task(prompt="fasse das zusammen", has_attachments=True))
    assert c.capability is Capability.CHAT
    assert c.complexity is Complexity.HIGH


def test_long_or_hard_prompt_is_high_complexity() -> None:
    hard = "Design a distributed architecture and prove its consistency. " * 20
    assert RuleRouter().classify(Task(prompt=hard)).complexity is Complexity.HIGH


def test_short_prompt_is_low_complexity() -> None:
    assert RuleRouter().classify(Task(prompt="hi")).complexity is Complexity.LOW


def test_german_hard_keywords_classify_high() -> None:
    c = RuleRouter().classify(
        Task(prompt="Analysiere die Architektur-Tradeoffs zwischen Microservices und Monolith.")
    )
    assert c.complexity is Complexity.HIGH


def test_german_code_keywords_classify_code() -> None:
    c = RuleRouter().classify(Task(prompt="Mein Skript wirft eine Fehlermeldung beim Start."))
    assert c.capability is Capability.CODE


def test_german_research_keywords_classify_research() -> None:
    c = RuleRouter().classify(Task(prompt="Was sind die neuesten Nachrichten zur Wahl?"))
    assert c.capability is Capability.RESEARCH


def test_photosynthese_is_not_multimodal() -> None:
    # "photo" must match as a word, not as a substring of Photosynthese
    c = RuleRouter().classify(Task(prompt="Erkläre mir die Photosynthese als Bericht."))
    assert c.capability is Capability.CHAT
