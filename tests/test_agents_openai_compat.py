"""Tests for the OpenAI-compatible adapter (used by OpenAI and Perplexity)."""

from __future__ import annotations

import json

import httpx
import respx

from cmn_ai.agents.openai_compat import OpenAICompatibleAgent
from cmn_ai.core import Bucket, Capability, CostPerMTok, Task

_PRICE = CostPerMTok(input_eur=0.5, output_eur=1.5)


def _make_agent(api_key: str | None = "sk-test") -> OpenAICompatibleAgent:
    return OpenAICompatibleAgent(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key=api_key,
        model="gpt-4o",
        cost_per_mtok=_PRICE,
        capabilities=frozenset({Capability.CHAT, Capability.CODE}),
        bucket=Bucket.GENERAL,
    )


@respx.mock
async def test_run_parses_response_and_computes_cost() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "42"}}],
                "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            },
        )
    )
    agent = _make_agent()

    response = await agent.run(Task(prompt="What is the answer?"))

    assert route.called
    assert response.text == "42"
    assert response.agent == "openai"
    assert response.usage.tokens_in == 1_000_000
    # 1M in * 0.5 + 1M out * 1.5 = 2.0
    assert response.cost_eur == 2.0


@respx.mock
async def test_run_sends_bearer_auth_and_system() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )

    respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=_capture)
    agent = _make_agent()

    await agent.run(Task(prompt="hi"), system="be terse")

    assert captured["auth"] == "Bearer sk-test"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["messages"][0] == {"role": "system", "content": "be terse"}


def test_inactive_without_api_key() -> None:
    assert _make_agent(api_key=None).active is False
    assert _make_agent(api_key="sk-test").active is True
