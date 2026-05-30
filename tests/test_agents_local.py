"""Tests for the local Ollama/Gemma agent (free, always-on)."""

from __future__ import annotations

import httpx
import respx

from cmn_ai.agents.local import OllamaAgent
from cmn_ai.core import Bucket, Capability, Message, Task


@respx.mock
async def test_run_parses_response_and_is_free() -> None:
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gemma4:latest",
                "message": {"role": "assistant", "content": "Hi there!"},
                "done": True,
                "prompt_eval_count": 26,
                "eval_count": 290,
            },
        )
    )
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")

    response = await agent.run(Task(prompt="Hello"))

    assert route.called
    assert response.text == "Hi there!"
    assert response.agent == "local"
    assert response.model == "gemma4:latest"
    assert response.usage.tokens_in == 26
    assert response.usage.tokens_out == 290
    assert response.cost_eur == 0.0
    assert response.bucket is Bucket.GENERAL


@respx.mock
async def test_run_sends_history_and_system() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}, "done": True},
        )

    respx.post("http://localhost:11434/api/chat").mock(side_effect=_capture)
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")

    task = Task(
        prompt="and now?",
        history=(Message(role="user", content="hi"), Message(role="assistant", content="hey")),
    )
    await agent.run(task, system="be terse")

    messages = captured["messages"]
    assert isinstance(messages, list)
    # system + 2 history turns + new prompt
    assert messages[0] == {"role": "system", "content": "be terse"}
    assert messages[-1] == {"role": "user", "content": "and now?"}
    assert len(messages) == 4


def test_local_agent_is_active_and_capable() -> None:
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")
    assert agent.active is True
    assert Capability.CHAT in agent.capabilities
    assert Capability.CODE in agent.capabilities
