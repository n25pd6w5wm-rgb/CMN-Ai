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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.config import Settings, load_settings
from cmn_ai.core import Message, RouteDecision, Task
from cmn_ai.orchestrator import Orchestrator
from cmn_ai.router.interface import Router
from cmn_ai.storage.conversations import ConversationStore
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
    conversations: ConversationStore | None = None


class ChatRequest(BaseModel):
    prompt: str
    conversation_id: int | None = None


class RaiseRequest(BaseModel):
    monthly_budget_eur: float


class RenameRequest(BaseModel):
    title: str


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
        # Cache-bust static assets by their mtime so CSS/JS edits show up immediately
        # (no stale browser cache) without manual version bumps.
        def _v(name: str) -> int:
            asset = _WEB_DIR / "static" / name
            return int(asset.stat().st_mtime) if asset.exists() else 0

        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"css_v": _v("chat.css"), "js_v": _v("chat.js")}
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            _WEB_DIR / "static" / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        # Served from root so the worker's scope covers the whole app.
        return FileResponse(
            _WEB_DIR / "static" / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

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
                    # Coding agent reports its tool-loop status; others have none.
                    "tools": getattr(agent, "tool_status", None),
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

    @app.get("/api/settings")
    async def settings_info() -> dict[str, Any]:
        s = state.settings
        return {
            "profile": s.profile,
            "router_strategy": s.router.strategy,
            "router_optimize": s.router.optimize,
            "monthly_budget_eur": state.governor.status().monthly_budget_eur,
        }

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

    def _require_store() -> ConversationStore:
        if state.conversations is None:
            raise HTTPException(503, "conversation store unavailable")
        return state.conversations

    @app.get("/api/conversations")
    async def list_conversations() -> dict[str, Any]:
        store = state.conversations
        return {"conversations": store.list_all() if store else []}

    @app.post("/api/conversations")
    async def new_conversation() -> dict[str, Any]:
        store = _require_store()
        return {"id": store.create(at=datetime.now(UTC)), "title": "New chat"}

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: int) -> dict[str, Any]:
        store = _require_store()
        if not store.exists(conversation_id):
            raise HTTPException(404, "conversation not found")
        return {"id": conversation_id, "messages": store.messages(conversation_id)}

    @app.patch("/api/conversations/{conversation_id}")
    async def rename_conversation(conversation_id: int, req: RenameRequest) -> dict[str, Any]:
        store = _require_store()
        if not store.exists(conversation_id):
            raise HTTPException(404, "conversation not found")
        store.rename(conversation_id, req.title)
        return {"id": conversation_id, "title": req.title}

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: int) -> dict[str, Any]:
        store = _require_store()
        store.delete(conversation_id)
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> StreamingResponse:
        store = state.conversations
        now = datetime.now(UTC)
        # Resolve (or open) the conversation and gather prior turns as context.
        cid: int | None = None
        history: tuple[Message, ...] = ()
        if store is not None:
            cid = (
                req.conversation_id
                if req.conversation_id and store.exists(req.conversation_id)
                else store.create(at=now)
            )
            history = tuple(
                Message(role=m["role"], content=m["content"])
                for m in store.messages(cid)
                if m["role"] in ("user", "assistant")
            )
            store.add_message(cid, "user", req.prompt, at=now)
        task = Task(prompt=req.prompt, history=history)

        async def stream() -> AsyncIterator[str]:
            if cid is not None:
                yield _sse("conversation", {"id": cid})
            decision, response = await state.orchestrator.handle(task)
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
            if store is not None and cid is not None:
                store.add_message(
                    cid,
                    "assistant",
                    response.text,
                    at=datetime.now(UTC),
                    agent=response.agent,
                    model=response.model,
                    cost_eur=response.cost_eur,
                )
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
    from cmn_ai.keystore import bootstrap_secrets

    settings = settings or load_settings()
    # Pull model API keys from Supabase (creds via local .env) and enable keyed agents.
    bootstrap_secrets(settings)
    db_path = settings.storage.resolved_path
    governor = BudgetGovernor(settings=settings.budget, ledger=Ledger(db_path))
    agents = build_agents(settings, governor)
    router = build_router(settings)
    decision_log = DecisionLog(db_path)
    conversations = ConversationStore(db_path)
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
        conversations=conversations,
    )


def build_router(settings: Settings) -> Router:
    """Pick the routing strategy: trained Dirigent (mlx/ollama) if available, else rules.

    Always returns something usable — if a trained strategy can't be loaded (adapter
    missing, MLX unavailable off Apple Silicon, Ollama unreachable), it logs and falls
    back to the deterministic rule router. The learned model only ever does
    classification; budget/agent selection always stays with the RuleRouter.
    """
    from cmn_ai.router.mlx_router import MLXRouter
    from cmn_ai.router.rule_router import RuleRouter

    # Optional local prompt optimiser (free Gemma via Ollama). Opt-in; the same rule
    # router is reused by the trained strategies, so they get optimisation via delegation.
    optimizer = None
    if settings.router.optimize and settings.router.optimizer_model:
        from cmn_ai.agents.local import OllamaAgent

        optimizer = OllamaAgent(host=settings.ollama_host, model=settings.router.optimizer_model)

    rule = RuleRouter(on_limit=settings.budget.on_limit, optimizer=optimizer)
    strategy = settings.router.strategy

    if strategy == "mlx":
        adapter = settings.router.resolved_adapter_path
        if not adapter.exists():
            print(f"[cmn-ai] router strategy=mlx but no adapter at {adapter}; using rule router.")
            return rule
        try:
            from cmn_ai.training.mlx_backend import build_generate_fn

            generate_fn = build_generate_fn(
                base_model=settings.router.base_model,
                adapter_path=adapter,
                max_new_tokens=settings.router.max_new_tokens,
            )
            print(f"[cmn-ai] trained Dirigent loaded ({settings.router.base_model} + adapter).")
            return MLXRouter(rule_router=rule, generate_fn=generate_fn)
        except Exception as exc:
            print(f"[cmn-ai] failed to load MLX router ({exc}); using rule router.")
            return rule

    if strategy == "ollama":
        try:
            from cmn_ai.router.ollama_backend import build_ollama_generate_fn

            generate_fn = build_ollama_generate_fn(
                model=settings.router.ollama_model,
                host=settings.ollama_host,
                max_new_tokens=settings.router.max_new_tokens,
            )
            print(f"[cmn-ai] trained Dirigent via Ollama ({settings.router.ollama_model}).")
            return MLXRouter(rule_router=rule, generate_fn=generate_fn)
        except Exception as exc:
            print(f"[cmn-ai] failed to build Ollama router ({exc}); using rule router.")
            return rule

    return rule


def create_app() -> FastAPI:
    """Production entry point used by uvicorn."""
    return build_app(build_state_from_settings())
