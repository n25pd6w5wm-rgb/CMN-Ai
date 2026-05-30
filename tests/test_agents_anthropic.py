"""Tests for the Anthropic (Claude) adapter — official SDK with prompt caching."""

from __future__ import annotations

import json

import httpx
import respx

from cmn_ai.agents.anthropic import AnthropicAgent
from cmn_ai.core import Bucket, Capability, CostPerMTok, Task

_PRICE = CostPerMTok(input_eur=3.0, output_eur=15.0)
_URL = "https://api.anthropic.com/v1/messages"


def _make_agent(api_key: str | None = "sk-ant-test") -> AnthropicAgent:
    return AnthropicAgent(
        api_key=api_key,
        model="claude-sonnet-4-6",
        cost_per_mtok=_PRICE,
        capabilities=frozenset({Capability.CHAT, Capability.CODE}),
        bucket=Bucket.GENERAL,
    )


def _message_response(text: str, tin: int, tout: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": tin, "output_tokens": tout},
        },
    )


@respx.mock
async def test_run_parses_response_and_cost() -> None:
    route = respx.post(_URL).mock(return_value=_message_response("hello", 1_000_000, 1_000_000))
    agent = _make_agent()

    response = await agent.run(Task(prompt="hi"))

    assert route.called
    assert response.text == "hello"
    assert response.agent == "anthropic"
    # 1M*3 + 1M*15 = 18
    assert response.cost_eur == 18.0


@respx.mock
async def test_system_prompt_carries_cache_control() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _message_response("ok", 1, 1)

    respx.post(_URL).mock(side_effect=_capture)
    agent = _make_agent()

    await agent.run(Task(prompt="hi"), system="stable system prompt")

    body = captured["body"]
    assert isinstance(body, dict)
    system = body["system"]
    # System sent as a cache-marked block so the stable prefix is cached.
    assert system[0]["text"] == "stable system prompt"
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_inactive_without_key() -> None:
    assert _make_agent(api_key=None).active is False
    assert _make_agent().active is True
