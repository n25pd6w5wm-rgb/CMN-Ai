"""Tests for the coding agent's agentic tool loop (read-only workspace)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import respx

from cmn_ai.agents.coding import CodingAgent
from cmn_ai.agents.workspace import WorkspaceTools
from cmn_ai.core import CostPerMTok, Task

_URL = "https://api.anthropic.com/v1/messages"
_PRICES = {"claude-sonnet-4-6": CostPerMTok(2.8, 13.8), "claude-opus-4-8": CostPerMTok(13.8, 69.0)}


def _agent(workspace: WorkspaceTools, **kw: object) -> CodingAgent:
    return CodingAgent(
        api_key="sk-ant-test",
        default_model="claude-sonnet-4-6",
        hard_model="claude-opus-4-8",
        price_lookup=_PRICES.__getitem__,
        workspace=workspace,
        **kw,  # type: ignore[arg-type]
    )


def _tool_use(tool: str, args: dict[str, object], tin: int, tout: int) -> dict[str, object]:
    return {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "tu_1", "name": tool, "input": args}],
        "usage": {"input_tokens": tin, "output_tokens": tout},
    }


def _final(text: str, tin: int, tout: int) -> dict[str, object]:
    return {
        "id": "m2",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": tin, "output_tokens": tout},
    }


@respx.mock
async def test_tool_loop_reads_file_then_answers(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("the answer is 42")
    bodies: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json=_tool_use("read_file", {"path": "a.txt"}, 10, 5)),
        httpx.Response(200, json=_final("The file says the answer is 42.", 20, 8)),
    ]

    def _route(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return responses[len(bodies) - 1]

    respx.post(_URL).mock(side_effect=_route)

    response = await _agent(WorkspaceTools(tmp_path)).run(Task(prompt="what is the answer?"))

    # Two round-trips: the tool call and the final answer.
    assert len(bodies) == 2
    # First request advertised the read-only tools.
    tools0 = cast(list[dict[str, str]], bodies[0]["tools"])
    assert {t["name"] for t in tools0} == {"read_file", "list_dir", "search"}
    # Second request fed the tool result (the file contents) back to the model.
    follow_up = bodies[1]["messages"][-1]  # type: ignore[index]
    tool_result = follow_up["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "the answer is 42" in tool_result["content"]
    # Final answer surfaced, usage summed across both calls.
    assert response.text == "The file says the answer is 42."
    assert response.usage.tokens_in == 30
    assert response.usage.tokens_out == 13


def test_tool_status_reflects_workspace(tmp_path: Path) -> None:
    none = CodingAgent(
        api_key="k",
        default_model="claude-sonnet-4-6",
        hard_model="claude-opus-4-8",
        price_lookup=_PRICES.__getitem__,
    )
    assert none.tool_status == {"workspace": False, "writable": False}
    assert _agent(WorkspaceTools(tmp_path)).tool_status == {"workspace": True, "writable": False}
    assert _agent(WorkspaceTools(tmp_path, writable=True)).tool_status == {
        "workspace": True,
        "writable": True,
    }


@respx.mock
async def test_tool_loop_advertises_write_tools_when_writable(tmp_path: Path) -> None:
    bodies: list[dict[str, object]] = []

    def _route(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_final("done", 1, 1))

    respx.post(_URL).mock(side_effect=_route)

    await _agent(WorkspaceTools(tmp_path, writable=True)).run(Task(prompt="make a change"))

    tools0 = cast(list[dict[str, str]], bodies[0]["tools"])
    assert {t["name"] for t in tools0} == {
        "read_file",
        "list_dir",
        "search",
        "write_file",
        "edit_file",
    }


@respx.mock
async def test_tool_loop_reports_tool_error_to_model(tmp_path: Path) -> None:
    bodies: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json=_tool_use("read_file", {"path": "../escape"}, 1, 1)),
        httpx.Response(200, json=_final("I could not read that.", 1, 1)),
    ]

    def _route(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return responses[len(bodies) - 1]

    respx.post(_URL).mock(side_effect=_route)

    await _agent(WorkspaceTools(tmp_path)).run(Task(prompt="read outside"))

    tool_result = bodies[1]["messages"][-1]["content"][0]  # type: ignore[index]
    assert tool_result["is_error"] is True
    assert "escapes" in tool_result["content"]


@respx.mock
async def test_tool_loop_stops_at_max_iterations(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    calls = {"n": 0}

    def _route(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_tool_use("read_file", {"path": "a.txt"}, 1, 1))

    respx.post(_URL).mock(side_effect=_route)

    await _agent(WorkspaceTools(tmp_path), max_iterations=3).run(Task(prompt="loop forever"))

    assert calls["n"] == 3  # hard cap honoured even when model never stops


@respx.mock
async def test_tool_loop_budget_guard_stops_before_calling(tmp_path: Path) -> None:
    calls = {"n": 0}

    def _route(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_final("hi", 1, 1))

    respx.post(_URL).mock(side_effect=_route)

    # Guard refuses immediately -> no model call is ever made.
    response = await _agent(WorkspaceTools(tmp_path), budget_guard=lambda _eur: False).run(
        Task(prompt="anything")
    )

    assert calls["n"] == 0
    assert "budget reached" in response.text
