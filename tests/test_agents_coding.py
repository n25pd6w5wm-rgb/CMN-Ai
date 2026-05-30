"""Tests for the dedicated coding agent (model tiering + coding bucket)."""

from __future__ import annotations

import json

import httpx
import respx

from cmn_ai.agents.coding import CodingAgent
from cmn_ai.core import Bucket, Capability, CostPerMTok, Task

_URL = "https://api.anthropic.com/v1/messages"
_PRICES = {
    "claude-sonnet-4-6": CostPerMTok(2.8, 13.8),
    "claude-opus-4-8": CostPerMTok(13.8, 69.0),
}


def _make_agent(api_key: str | None = "sk-ant-test") -> CodingAgent:
    return CodingAgent(
        api_key=api_key,
        default_model="claude-sonnet-4-6",
        hard_model="claude-opus-4-8",
        price_lookup=_PRICES.__getitem__,
    )


def _resp(model: str, tin: int, tout: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": model,
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "code"}],
            "usage": {"input_tokens": tin, "output_tokens": tout},
        },
    )


def test_identity_is_coding_bucket_and_capability() -> None:
    agent = _make_agent()
    assert agent.name == "coding"
    assert agent.bucket is Bucket.CODING
    assert agent.capabilities == frozenset({Capability.CODE})


@respx.mock
async def test_easy_task_uses_default_model() -> None:
    captured: dict[str, object] = {}

    def _cap(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return _resp("claude-sonnet-4-6", 1_000_000, 1_000_000)

    respx.post(_URL).mock(side_effect=_cap)
    agent = _make_agent()

    response = await agent.run(Task(prompt="rename a variable"))

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "claude-sonnet-4-6"
    assert response.model == "claude-sonnet-4-6"
    # sonnet price: 2.8 + 13.8 = 16.6
    assert response.cost_eur == 16.6
    assert response.bucket is Bucket.CODING


@respx.mock
async def test_hard_task_escalates_to_opus() -> None:
    captured: dict[str, object] = {}

    def _cap(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return _resp("claude-opus-4-8", 1_000_000, 1_000_000)

    respx.post(_URL).mock(side_effect=_cap)
    agent = _make_agent()

    response = await agent.run(
        Task(prompt="Refactor and design a distributed architecture:\n```py\nx=1\n```")
    )

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "claude-opus-4-8"
    assert response.model == "claude-opus-4-8"
    # opus price: 13.8 + 69.0 = 82.8
    assert response.cost_eur == 82.8


@respx.mock
async def test_coding_system_prompt_is_cached() -> None:
    captured: dict[str, object] = {}

    def _cap(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return _resp("claude-sonnet-4-6", 1, 1)

    respx.post(_URL).mock(side_effect=_cap)
    agent = _make_agent()

    await agent.run(Task(prompt="fix a typo"))

    body = captured["body"]
    assert isinstance(body, dict)
    system = body["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "code" in system[0]["text"].lower()


def test_inactive_without_key() -> None:
    assert _make_agent(api_key=None).active is False
    assert _make_agent().active is True
