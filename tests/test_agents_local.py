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


def test_local_agent_is_capable_but_starts_inactive() -> None:
    # Pessimistic start: an unreachable Pi must never receive the first request.
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")
    assert agent.active is False
    assert Capability.CHAT in agent.capabilities
    assert Capability.CODE in agent.capabilities


# ---------- health check: active reflects actual Ollama reachability ----------


@respx.mock
async def test_refresh_health_activates_on_ok() -> None:
    respx.get("http://localhost:11434/api/tags").mock(return_value=httpx.Response(200, json={}))
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")

    await agent.refresh_health()

    assert agent.active is True


@respx.mock
async def test_refresh_health_deactivates_on_connect_error() -> None:
    respx.get("http://localhost:11434/api/tags").mock(side_effect=httpx.ConnectError("down"))
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")

    await agent.refresh_health()

    assert agent.active is False


@respx.mock
async def test_health_result_cached_within_ttl() -> None:
    route = respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={})
    )
    clock = {"t": 100.0}
    agent = OllamaAgent(
        host="http://localhost:11434", model="gemma4:latest", now=lambda: clock["t"]
    )

    await agent.refresh_health()
    clock["t"] += 10.0  # inside the 30s TTL
    await agent.refresh_health()

    assert route.call_count == 1
    assert agent.active is True


# ---------- dynamic model selection: prefer Gemma 4, fall back to Gemma 3 ----------


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


@respx.mock
async def test_health_check_upgrades_to_gemma4_when_pulled() -> None:
    # Pulling a Gemma 4 on the host upgrades the agent without a config change;
    # a Gemma-3-only host (the Pi today) keeps working untouched.
    respx.get("http://localhost:11434/api/tags").mock(return_value=_tags("gemma3:1b", "gemma4:e2b"))
    agent = OllamaAgent(host="http://localhost:11434", model="gemma3:1b")

    await agent.refresh_health()

    assert agent.active is True
    assert agent.model == "gemma4:e2b"


@respx.mock
async def test_health_check_falls_back_to_gemma3_when_no_gemma4() -> None:
    respx.get("http://localhost:11434/api/tags").mock(return_value=_tags("gemma3:1b"))
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:e4b")

    await agent.refresh_health()

    assert agent.model == "gemma3:1b"


@respx.mock
async def test_health_check_keeps_configured_model_within_best_generation() -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=_tags("gemma4:e2b", "gemma4:e4b")
    )
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:e2b")

    await agent.refresh_health()

    assert agent.model == "gemma4:e2b"


@respx.mock
async def test_health_check_picks_biggest_tag_when_configured_is_missing() -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=_tags("gemma4:e2b", "gemma4:e4b")
    )
    agent = OllamaAgent(host="http://localhost:11434", model="gemma4:latest")

    await agent.refresh_health()

    assert agent.model == "gemma4:e4b"


@respx.mock
async def test_health_check_respects_non_gemma_model_that_is_present() -> None:
    # An explicitly configured non-Gemma model is a deliberate choice — keep it.
    respx.get("http://localhost:11434/api/tags").mock(return_value=_tags("qwen3:4b", "gemma4:e2b"))
    agent = OllamaAgent(host="http://localhost:11434", model="qwen3:4b")

    await agent.refresh_health()

    assert agent.model == "qwen3:4b"


@respx.mock
async def test_health_check_keeps_configured_model_when_tags_are_empty() -> None:
    respx.get("http://localhost:11434/api/tags").mock(return_value=httpx.Response(200, json={}))
    agent = OllamaAgent(host="http://localhost:11434", model="gemma3:1b")

    await agent.refresh_health()

    assert agent.active is True
    assert agent.model == "gemma3:1b"


@respx.mock
async def test_health_rechecked_after_ttl_expiry() -> None:
    route = respx.get("http://localhost:11434/api/tags").mock(
        side_effect=[httpx.Response(200, json={}), httpx.ConnectError("down")]
    )
    clock = {"t": 100.0}
    agent = OllamaAgent(
        host="http://localhost:11434", model="gemma4:latest", now=lambda: clock["t"]
    )

    await agent.refresh_health()
    assert agent.active is True
    clock["t"] += 31.0  # past the TTL — the dead Pi must be noticed
    await agent.refresh_health()

    assert route.call_count == 2
    assert agent.active is False
