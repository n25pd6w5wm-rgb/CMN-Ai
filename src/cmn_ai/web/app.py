"""FastAPI application: JSON endpoints + an SSE chat stream + the chat UI.

The app is built from an ``AppState`` so it can be wired with real agents in
production or fakes in tests. Endpoints expose routing transparency (``/api/route-debug``),
the model roster (``/api/models``), live budget (``/api/budget`` + ``/raise``), and the
chat stream (``/api/chat``). Streaming uses Server-Sent Events: a ``route`` event with
the decision, incremental ``delta`` events, then ``done`` — or a ``blocked`` event when
the budget governor refuses a paid call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.config import Settings, load_settings
from cmn_ai.core import RouteDecision, Task
from cmn_ai.orchestrator import Orchestrator
from cmn_ai.router.interface import Router
from cmn_ai.storage.decisions import DecisionLog

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


@dataclass
class AppState:
    """Everything one running instance needs, injectable for tests."""

    settings: Settings
    agents: Mapping[str, Agent]
    router: Router
    governor: BudgetGovernor
    orchestrator: Orchestrator
    decision_log: DecisionLog | None = None


class ChatRequest(BaseModel):
    prompt: str


class RaiseRequest(BaseModel):
    monthly_budget_eur: float


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _decision_dict(decision: RouteDecision) -> dict[str, Any]:
    c = decision.classification
    return {
        "agent": decision.agent,
        "model": decision.model,
        "reason": decision.reason,
        "estimated_eur": decision.estimated_eur,
        "fell_back": decision.fell_back,
        "blocked": decision.blocked,
        "classification": {
            "capability": c.capability.value,
            "complexity": c.complexity.value,
            "needs_web": c.needs_web,
            "bucket": c.bucket.value,
        },
    }


def build_app(state: AppState) -> FastAPI:
    app = FastAPI(title="cmn-ai")
    app.state.cmn = state

    static_dir = _WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(request, "index.html")

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {
            "models": [
                {
                    "name": agent.name,
                    "model": agent.model,
                    "active": agent.active,
                    "bucket": agent.bucket.value,
                    "capabilities": sorted(c.value for c in agent.capabilities),
                    "cost_per_mtok": {
                        "input_eur": agent.cost_per_mtok.input_eur,
                        "output_eur": agent.cost_per_mtok.output_eur,
                    },
                }
                for agent in state.agents.values()
            ]
        }

    @app.get("/api/budget")
    async def budget() -> dict[str, Any]:
        status = state.governor.status()
        return {
            "monthly_budget_eur": status.monthly_budget_eur,
            "weekly_budget_eur": status.weekly_budget_eur,
            "buckets": [
                {
                    "bucket": b.bucket.value,
                    "cap_eur": b.cap_eur,
                    "spent_eur": b.spent_eur,
                    "remaining_eur": b.remaining_eur,
                }
                for b in status.buckets.values()
            ],
        }

    @app.post("/api/budget/raise")
    async def raise_budget(req: RaiseRequest) -> dict[str, Any]:
        state.governor.raise_budget(req.monthly_budget_eur)
        return {"monthly_budget_eur": req.monthly_budget_eur}

    @app.get("/api/analytics")
    async def analytics() -> dict[str, Any]:
        if state.decision_log is None:
            return {"by_agent": [], "total_count": 0, "total_eur": 0.0}
        return state.decision_log.summary()

    @app.get("/api/decisions")
    async def decisions(limit: int = 50) -> dict[str, Any]:
        if state.decision_log is None:
            return {"decisions": []}
        return {"decisions": state.decision_log.recent(limit=limit)}

    @app.post("/api/route-debug")
    async def route_debug(req: ChatRequest) -> dict[str, Any]:
        decision = state.orchestrator.route(Task(prompt=req.prompt))
        return _decision_dict(decision)

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            decision, response = await state.orchestrator.handle(Task(prompt=req.prompt))
            yield _sse("route", _decision_dict(decision))
            if response is None:
                yield _sse(
                    "blocked",
                    {"reason": decision.reason, "bucket": decision.classification.bucket.value},
                )
                yield _sse("done", {"blocked": True})
                return
            for word in response.text.split(" "):
                yield _sse("delta", {"text": word + " "})
            yield _sse(
                "done",
                {
                    "agent": response.agent,
                    "model": response.model,
                    "cost_eur": response.cost_eur,
                    "tokens_in": response.usage.tokens_in,
                    "tokens_out": response.usage.tokens_out,
                },
            )

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def build_state_from_settings(settings: Settings | None = None) -> AppState:
    """Wire real agents, router, governor and orchestrator from configuration."""
    from cmn_ai.agents.factory import build_agents
    from cmn_ai.budget.ledger import Ledger
    from cmn_ai.router.rule_router import RuleRouter

    settings = settings or load_settings()
    db_path = settings.storage.resolved_path
    agents = build_agents(settings)
    governor = BudgetGovernor(settings=settings.budget, ledger=Ledger(db_path))
    router = RuleRouter(on_limit=settings.budget.on_limit)
    decision_log = DecisionLog(db_path)
    orchestrator = Orchestrator(
        agents=agents, router=router, governor=governor, decision_log=decision_log
    )
    return AppState(
        settings=settings,
        agents=agents,
        router=router,
        governor=governor,
        orchestrator=orchestrator,
        decision_log=decision_log,
    )


def create_app() -> FastAPI:
    """Production entry point used by uvicorn."""
    return build_app(build_state_from_settings())
