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
    orchestrator = Orchestrator(agents=agents, router=router, governor=governor)
    state = AppState(
        settings=settings,
        agents=agents,
        router=router,
        governor=governor,
        orchestrator=orchestrator,
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
