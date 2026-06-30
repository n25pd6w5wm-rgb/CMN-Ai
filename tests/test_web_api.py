"""Tests for the FastAPI JSON/SSE endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings, Settings
from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage
from cmn_ai.orchestrator import Orchestrator
from cmn_ai.router.rule_router import RuleRouter
from cmn_ai.storage.conversations import ConversationStore
from cmn_ai.storage.decisions import DecisionLog
from cmn_ai.web.app import AppState, build_app

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
FREE = CostPerMTok(0.0, 0.0)


class FakeAgent:
    def __init__(
        self, name: str, *, capabilities: set[Capability], cost: CostPerMTok, bucket: Bucket
    ) -> None:
        self.name = name
        self.model = "gemma4:latest" if name == "local" else "claude-sonnet-4-6"
        self.capabilities = frozenset(capabilities)
        self.cost_per_mtok = cost
        self.bucket = bucket
        self.active = True

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        return AgentResponse(
            text="hello world from agent",
            agent=self.name,
            model=self.model,
            usage=Usage(10, 20),
            cost_eur=0.0 if self.cost_per_mtok == FREE else 0.5,
            bucket=self.bucket,
        )


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(profile="mac", budget=BudgetSettings(monthly_budget_eur=30.0))
    agents = {
        "local": FakeAgent(
            "local",
            capabilities={Capability.CHAT, Capability.CODE},
            cost=FREE,
            bucket=Bucket.GENERAL,
        ),
        "coding": FakeAgent(
            "coding",
            capabilities={Capability.CODE},
            cost=CostPerMTok(2.8, 13.8),
            bucket=Bucket.CODING,
        ),
    }
    governor = BudgetGovernor(
        settings=settings.budget, ledger=Ledger(tmp_path / "l.db"), now=lambda: NOW
    )
    router = RuleRouter()
    decision_log = DecisionLog(tmp_path / "d.db")
    orchestrator = Orchestrator(
        agents=agents, router=router, governor=governor, decision_log=decision_log, now=lambda: NOW
    )
    state = AppState(
        settings=settings,
        agents=agents,
        router=router,
        governor=governor,
        orchestrator=orchestrator,
        decision_log=decision_log,
        conversations=ConversationStore(tmp_path / "conv.db"),
    )
    return TestClient(build_app(state))


def test_models_lists_agents(tmp_path: Path) -> None:
    client = _client(tmp_path)
    data = client.get("/api/models").json()
    names = {m["name"] for m in data["models"]}
    assert names == {"local", "coding"}
    local = next(m for m in data["models"] if m["name"] == "local")
    assert local["active"] is True
    assert "chat" in local["capabilities"]
    # Every model carries a "tools" field; non-coding agents report none.
    assert all("tools" in m for m in data["models"])
    assert local["tools"] is None


def test_budget_reports_caps(tmp_path: Path) -> None:
    client = _client(tmp_path)
    data = client.get("/api/budget").json()
    assert data["monthly_budget_eur"] == 30.0
    coding = next(b for b in data["buckets"] if b["bucket"] == "coding")
    assert round(coding["cap_eur"], 2) == 2.8


def test_route_debug_classifies_without_running(tmp_path: Path) -> None:
    client = _client(tmp_path)
    data = client.post("/api/route-debug", json={"prompt": "hello"}).json()
    assert data["agent"] == "local"
    assert data["classification"]["capability"] == "chat"
    assert data["blocked"] is False


def test_budget_raise_scales_caps(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/budget/raise", json={"monthly_budget_eur": 60.0})
    data = client.get("/api/budget").json()
    assert data["monthly_budget_eur"] == 60.0


def test_chat_streams_route_and_answer(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with client.stream("POST", "/api/chat", json={"prompt": "hello there"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: route" in body
    assert "event: done" in body
    # reconstruct the answer from the per-word delta events
    deltas = [
        json.loads(line[len("data: ") :])["text"]
        for line in body.splitlines()
        if line.startswith("data: ") and '"text"' in line
    ]
    assert "".join(deltas).strip() == "hello world from agent"


def test_analytics_reflects_a_chat(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with client.stream("POST", "/api/chat", json={"prompt": "hello there"}) as resp:
        "".join(resp.iter_text())
    data = client.get("/api/analytics").json()
    assert data["total_count"] == 1
    assert data["total_eur"] == 0.0
    assert any(a["agent"] == "local" for a in data["by_agent"])


def test_chat_blocked_emits_blocked_event(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # exhaust the coding bucket so a hard code task is blocked
    client_state_governor = client.app.state.cmn  # type: ignore[attr-defined]
    client_state_governor.governor.record(Bucket.CODING, "x", "m", Usage(0, 0), eur=1000.0)
    with client.stream(
        "POST", "/api/chat", json={"prompt": "refactor this complex module"}
    ) as resp:
        body = "".join(resp.iter_text())
    assert "event: blocked" in body


# ---------- conversations & multi-turn history ----------


def _events(text: str) -> list[tuple[str | None, dict[str, object]]]:
    out: list[tuple[str | None, dict[str, object]]] = []
    for block in text.split("\n\n"):
        event: str | None = None
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((event, json.loads(data)))
    return out


def test_new_conversation_and_empty_list(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/conversations").json()["conversations"] == []
    created = client.post("/api/conversations").json()
    assert created["id"] >= 1
    rows = client.get("/api/conversations").json()["conversations"]
    assert len(rows) == 1 and rows[0]["title"] == "New chat"


def test_chat_persists_messages_and_autotitles(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with client.stream("POST", "/api/chat", json={"prompt": "hello there friend"}) as resp:
        body = "".join(resp.iter_text())
    conv_events = [d for ev, d in _events(body) if ev == "conversation"]
    assert conv_events, "chat should announce its conversation id"
    cid = conv_events[0]["id"]

    convo = client.get(f"/api/conversations/{cid}").json()
    roles = [m["role"] for m in convo["messages"]]
    assert roles == ["user", "assistant"]
    assert convo["messages"][1]["agent"] == "local"
    assert (
        client.get("/api/conversations").json()["conversations"][0]["title"] == "hello there friend"
    )


def test_chat_feeds_prior_history_as_context(tmp_path: Path) -> None:
    settings = Settings(profile="mac", budget=BudgetSettings(monthly_budget_eur=30.0))

    class RecordingAgent:
        def __init__(self) -> None:
            self.name = "local"
            self.model = "m"
            self.capabilities = frozenset({Capability.CHAT, Capability.CODE})
            self.cost_per_mtok = FREE
            self.bucket = Bucket.GENERAL
            self.active = True
            self.history_lengths: list[int] = []

        async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
            self.history_lengths.append(len(task.history))
            return AgentResponse(
                text="ok",
                agent=self.name,
                model=self.model,
                usage=Usage(1, 1),
                cost_eur=0.0,
                bucket=self.bucket,
            )

    rec = RecordingAgent()
    agents = {"local": rec}
    governor = BudgetGovernor(
        settings=settings.budget, ledger=Ledger(tmp_path / "l.db"), now=lambda: NOW
    )
    orchestrator = Orchestrator(
        agents=agents, router=RuleRouter(), governor=governor, now=lambda: NOW
    )
    state = AppState(
        settings=settings,
        agents=agents,
        router=RuleRouter(),
        governor=governor,
        orchestrator=orchestrator,
        conversations=ConversationStore(tmp_path / "conv.db"),
    )
    client = TestClient(build_app(state))

    with client.stream("POST", "/api/chat", json={"prompt": "first"}) as resp:
        body = "".join(resp.iter_text())
    cid = next(d["id"] for ev, d in _events(body) if ev == "conversation")
    with client.stream("POST", "/api/chat", json={"prompt": "second", "conversation_id": cid}):
        pass

    # First turn has no history; second turn sees the prior user+assistant pair.
    assert rec.history_lengths == [0, 2]


def test_rename_and_delete_conversation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    cid = client.post("/api/conversations").json()["id"]
    assert client.patch(f"/api/conversations/{cid}", json={"title": "Pi notes"}).status_code == 200
    assert client.get("/api/conversations").json()["conversations"][0]["title"] == "Pi notes"
    client.delete(f"/api/conversations/{cid}")
    assert client.get("/api/conversations").json()["conversations"] == []


def test_get_missing_conversation_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/conversations/9999").status_code == 404


def test_settings_info(tmp_path: Path) -> None:
    client = _client(tmp_path)
    data = client.get("/api/settings").json()
    assert data["profile"] == "mac"
    assert data["router_strategy"] in {"rule", "mlx", "ollama"}
    assert data["monthly_budget_eur"] == 30.0
    assert "router_optimize" in data


def test_manifest_is_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.json()["short_name"] == "cmn·ai"
    assert r.json()["display"] == "standalone"


def test_service_worker_served_at_root_scope(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers.get("service-worker-allowed") == "/"
