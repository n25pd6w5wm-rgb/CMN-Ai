"""Tests for the Google Gemini adapter."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cmn_ai.agents.gemini import GeminiAgent
from cmn_ai.core import Bucket, Capability, CostPerMTok, Message, Task

_PRICE = CostPerMTok(input_eur=1.0, output_eur=2.0)
_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"


def _make_agent(api_key: str | None = "g-test") -> GeminiAgent:
    return GeminiAgent(
        api_key=api_key,
        model="gemini-1.5-pro",
        cost_per_mtok=_PRICE,
        capabilities=frozenset({Capability.CHAT, Capability.MULTIMODAL}),
        bucket=Bucket.GENERAL,
    )


@respx.mock
async def test_run_parses_response_and_cost() -> None:
    route = respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "blue"}], "role": "model"}}],
                "usageMetadata": {
                    "promptTokenCount": 1_000_000,
                    "candidatesTokenCount": 500_000,
                },
            },
        )
    )
    agent = _make_agent()

    response = await agent.run(Task(prompt="sky color?"))

    assert route.called
    assert response.text == "blue"
    assert response.agent == "gemini"
    # 1M*1.0 + 0.5M*2.0 = 2.0
    assert response.cost_eur == 2.0
    assert response.usage.tokens_out == 500_000


@respx.mock
async def test_maps_assistant_role_to_model_and_sends_system() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["key"] = request.url.params.get("key")
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )

    respx.post(_URL).mock(side_effect=_capture)
    agent = _make_agent()

    task = Task(
        prompt="continue",
        history=(Message(role="assistant", content="earlier"),),
    )
    await agent.run(task, system="be terse")

    body = captured["body"]
    assert isinstance(body, dict)
    assert captured["key"] == "g-test"
    assert body["systemInstruction"] == {"parts": [{"text": "be terse"}]}
    # assistant -> model role mapping
    assert body["contents"][0] == {"role": "model", "parts": [{"text": "earlier"}]}
    assert body["contents"][-1] == {"role": "user", "parts": [{"text": "continue"}]}


def test_inactive_without_key() -> None:
    assert _make_agent(api_key=None).active is False


@respx.mock
async def test_run_joins_multiple_text_parts() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "Hallo "}, {"text": "Welt"}]}}]},
        )
    )

    response = await _make_agent().run(Task(prompt="gruß"))

    assert response.text == "Hallo Welt"


@respx.mock
async def test_run_skips_thought_parts() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "internal reasoning", "thought": True},
                                {"text": "Antwort"},
                            ]
                        }
                    }
                ]
            },
        )
    )

    response = await _make_agent().run(Task(prompt="frage"))

    assert response.text == "Antwort"


@respx.mock
async def test_run_raises_when_no_candidates() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={"promptFeedback": {"blockReason": "SAFETY"}},
        )
    )

    with pytest.raises(RuntimeError, match="SAFETY"):
        await _make_agent().run(Task(prompt="frage"))


@respx.mock
async def test_run_raises_when_parts_empty_for_safety_finish() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {}, "finishReason": "SAFETY"}]},
        )
    )

    with pytest.raises(RuntimeError, match="SAFETY"):
        await _make_agent().run(Task(prompt="frage"))


@respx.mock
async def test_run_returns_partial_text_on_max_tokens_finish() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Angefangen aber"}]},
                        "finishReason": "MAX_TOKENS",
                    }
                ]
            },
        )
    )

    response = await _make_agent().run(Task(prompt="frage"))

    assert response.text == "Angefangen aber"
