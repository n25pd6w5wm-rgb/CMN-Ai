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
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from cmn_ai.agents.base import Agent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import LedgerBackend
from cmn_ai.budget.supabase_ledger import SupabaseLedger
from cmn_ai.config import Settings, load_settings
from cmn_ai.core import Message, RouteDecision, Task
from cmn_ai.orchestrator import Orchestrator, wants_council
from cmn_ai.router.interface import Router
from cmn_ai.storage.conversations import ConversationBackend, ConversationStore
from cmn_ai.storage.decisions import DecisionLog
from cmn_ai.storage.supabase_store import SupabaseConversationStore
from cmn_ai.web.attachments import Attachment, is_image, mime_for, render_attachments
from cmn_ai.web.auth import AuthError, SupabaseAuth, build_auth_from_env
from cmn_ai.web.deliverables import FileBackend, FileStore, extract_deliverables
from cmn_ai.web.supabase_files import SupabaseFileStore
from cmn_ai.web.vault_client import VaultClient, build_vault_client_from_env

SESSION_COOKIE = "cmn_session"

_MAX_IMAGES = 4  # per request, keeps vision payloads bounded

# Legal-page contents (DE). The Impressum placeholders must be filled with the
# operator's real details before commercial launch — a lawyer should review both.
_IMPRESSUM_BODY = """
<h2>Angaben gemäß § 5 DDG</h2>
<p class="placeholder">[Vor- und Nachname bzw. Firma]<br>[Straße Hausnummer]<br>
[PLZ Ort]<br>Deutschland</p>
<h2>Kontakt</h2>
<p class="placeholder">E-Mail: [kontakt@domain.tld]</p>
<h2>Verantwortlich für den Inhalt</h2>
<p class="placeholder">[Name, Anschrift wie oben]</p>
<p><em>Hinweis: Dieses Impressum ist ein Entwurf und vor dem kommerziellen Start zu
vervollständigen und juristisch zu prüfen.</em></p>
"""

_DATENSCHUTZ_BODY = """
<p><em>Stand: Juli 2026 — Entwurf, vor dem kommerziellen Start juristisch prüfen lassen.</em></p>
<h2>1. Verantwortlicher</h2>
<p class="placeholder">[Name und Kontaktdaten wie im Impressum]</p>
<h2>2. Welche Daten wir verarbeiten</h2>
<ul>
<li><strong>Konto:</strong> E-Mail-Adresse und Passwort-Hash, gespeichert bei unserem
Auth-Dienstleister Supabase (EU-Region), zur Anmeldung und Kontoverwaltung
(Art. 6 Abs. 1 lit. b DSGVO).</li>
<li><strong>Konversationen:</strong> Deine Chat-Nachrichten und die KI-Antworten werden
deinem Konto zugeordnet gespeichert, damit du sie wieder öffnen kannst. Du kannst
Konversationen jederzeit in der App löschen.</li>
<li><strong>KI-Verarbeitung:</strong> Zur Beantwortung wird der Inhalt deiner Nachricht an
das jeweils gewählte KI-Modell übermittelt — lokal betriebene Modelle oder
API-Anbieter (Anthropic, OpenAI, Google, Perplexity). Welche KI geantwortet hat,
zeigt die App unter jeder Antwort an.</li>
<li><strong>Session-Cookie:</strong> Ein technisch notwendiges Cookie hält dich angemeldet.
Es gibt kein Tracking, keine Werbe-Cookies, keine Analyse-Pixel.</li>
</ul>
<h2>3. Hosting</h2>
<p>Die App läuft bei Render (Cloud-Hosting). Beim Aufruf fallen technisch notwendige
Server-Logs an (IP-Adresse, Zeitpunkt, aufgerufene Seite), die zur Betriebssicherheit
kurzzeitig gespeichert werden (Art. 6 Abs. 1 lit. f DSGVO).</p>
<h2>4. Deine Rechte</h2>
<p>Du hast das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der
Verarbeitung, Datenübertragbarkeit und Widerspruch (Art. 15 bis 21 DSGVO) sowie auf
Beschwerde bei einer Aufsichtsbehörde. Schreib dazu an die im Impressum genannte
Adresse.</p>
"""

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
    conversations: ConversationBackend | None = None
    auth: SupabaseAuth | None = None
    vault: VaultClient | None = None
    files: FileBackend = field(default_factory=FileStore)


class ChatRequest(BaseModel):
    prompt: str
    conversation_id: str | None = None
    agent: str | None = None  # optional user-selected agent/model (else the router decides)
    mode: str = "spar"  # auto-team eagerness: "spar" (budget-conscious) | "power" (team fast)
    attachments: list[Attachment] | None = None  # uploaded files (base64), max 8


class BenchmarkRequest(BaseModel):
    prompt: str | None = None


class ExportRequest(BaseModel):
    content: str
    format: Literal["pdf", "pptx", "docx"]
    filename: str | None = None  # without extension
    theme: str | None = None  # optional visual theme (report | modern | elegant | deck)


class RaiseRequest(BaseModel):
    monthly_budget_eur: float


class RenameRequest(BaseModel):
    title: str


class AuthRequest(BaseModel):
    email: str
    password: str


class RecoverRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    access_token: str
    password: str


class VaultNote(BaseModel):
    path: str
    content: str


class VaultUploadRequest(BaseModel):
    notes: list[VaultNote]


def _set_session(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 24 * 14,
        path="/",
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
}


def _render_document(fmt: str, content: str, theme: str = "") -> bytes:
    from cmn_ai.web.export import to_docx, to_pdf, to_pptx

    if fmt == "pdf":
        return to_pdf(content, theme=theme)
    if fmt == "pptx":
        return to_pptx(content, theme=theme)
    if fmt == "docx":
        return to_docx(content, theme=theme)
    return content.encode("utf-8")  # markdown / plain-text fallback


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

    def _uid(request: Request) -> str | None:
        """The logged-in user's id (multi-user mode), or None (open mode)."""
        user = getattr(request.state, "user", None)
        return str(user["id"]) if user else None

    # Paths reachable without a session (so the login wall + healthcheck + PWA work).
    _open_paths = {
        "/login",
        "/reset",
        "/welcome",
        "/landing",
        "/impressum",
        "/datenschutz",
        "/healthz",
        "/manifest.webmanifest",
        "/sw.js",
        "/favicon.ico",
    }

    @app.middleware("http")
    async def auth_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if state.auth is None:  # open single-user mode
            return await call_next(request)
        path = request.url.path
        if path in _open_paths or path.startswith("/static") or path.startswith("/api/auth"):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        user = await state.auth.get_user(token) if token else None
        if user is None:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "unauthenticated"}, status_code=401)
            # Visitors hit the marketing page first; the login is one click away.
            return RedirectResponse("/welcome" if path == "/" else "/login", status_code=302)
        request.state.user = user
        return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
    async def landing(request: Request) -> HTMLResponse:
        # Public marketing/landing page (main domain); links to the app at "/".
        css = _WEB_DIR / "static" / "chat.css"
        css_v = int(css.stat().st_mtime) if css.exists() else 0
        return _TEMPLATES.TemplateResponse(request, "landing.html", {"css_v": css_v})

    @app.get("/landing", include_in_schema=False)
    async def landing_alias() -> RedirectResponse:
        return RedirectResponse("/welcome", status_code=302)

    def _legal_page(request: Request, title: str, body: str) -> HTMLResponse:
        css = _WEB_DIR / "static" / "chat.css"
        css_v = int(css.stat().st_mtime) if css.exists() else 0
        return _TEMPLATES.TemplateResponse(
            request, "legal.html", {"title": title, "body": body, "css_v": css_v}
        )

    @app.get("/impressum", response_class=HTMLResponse, include_in_schema=False)
    async def impressum(request: Request) -> HTMLResponse:
        return _legal_page(request, "Impressum", _IMPRESSUM_BODY)

    @app.get("/datenschutz", response_class=HTMLResponse, include_in_schema=False)
    async def datenschutz(request: Request) -> HTMLResponse:
        return _legal_page(request, "Datenschutzerklärung", _DATENSCHUTZ_BODY)

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request) -> Response:
        if state.auth is None:  # no auth configured → nothing to log into
            return RedirectResponse("/", status_code=302)
        token = request.cookies.get(SESSION_COOKIE)
        if token and await state.auth.get_user(token):
            return RedirectResponse("/", status_code=302)

        def _v(name: str) -> int:
            asset = _WEB_DIR / "static" / name
            return int(asset.stat().st_mtime) if asset.exists() else 0

        return _TEMPLATES.TemplateResponse(request, "login.html", {"css_v": _v("chat.css")})

    @app.post("/api/auth/login")
    async def auth_login(req: AuthRequest, request: Request, response: Response) -> dict[str, Any]:
        if state.auth is None:
            raise HTTPException(400, "authentication is not configured")
        try:
            session = await state.auth.sign_in(req.email, req.password)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        token = session.get("access_token")
        if not token:
            raise HTTPException(401, "no access token returned")
        _set_session(response, str(token), secure=request.url.scheme == "https")
        return {"user": {"email": (session.get("user") or {}).get("email")}}

    @app.post("/api/auth/signup")
    async def auth_signup(req: AuthRequest, request: Request, response: Response) -> dict[str, Any]:
        # Registration is closed by default so only existing accounts can spend the
        # configured API budget. Re-open it by setting CMN_ALLOW_SIGNUPS=1.
        if os.environ.get("CMN_ALLOW_SIGNUPS") != "1":
            raise HTTPException(
                403, "Registrierung ist geschlossen — nur bestehende Konten haben Zugang."
            )
        if state.auth is None:
            raise HTTPException(400, "authentication is not configured")
        try:
            result = await state.auth.sign_up(req.email, req.password)
        except AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = result.get("access_token")
        if token:  # confirmations disabled → already signed in
            _set_session(response, str(token), secure=request.url.scheme == "https")
            return {"user": {"email": (result.get("user") or {}).get("email")}, "signed_in": True}
        return {"signed_in": False, "confirm_email": True}

    @app.post("/api/auth/recover")
    async def auth_recover(req: RecoverRequest, request: Request) -> dict[str, Any]:
        # Always answers ok — whether the address exists must stay unobservable.
        if state.auth is not None:
            await state.auth.recover(req.email, redirect_to=f"{request.base_url}reset")
        return {"ok": True}

    @app.post("/api/auth/reset")
    async def auth_reset(req: ResetRequest) -> dict[str, Any]:
        if state.auth is None:
            raise HTTPException(400, "authentication is not configured")
        try:
            await state.auth.update_password(req.access_token, req.password)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        return {"ok": True}

    @app.get("/reset", response_class=HTMLResponse, include_in_schema=False)
    async def reset_page(request: Request) -> HTMLResponse:
        css = _WEB_DIR / "static" / "chat.css"
        css_v = int(css.stat().st_mtime) if css.exists() else 0
        return _TEMPLATES.TemplateResponse(request, "reset.html", {"css_v": css_v})

    @app.post("/api/auth/logout")
    async def auth_logout(response: Response) -> dict[str, Any]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        if state.auth is None:
            return {"user": None, "auth_enabled": False}
        token = request.cookies.get(SESSION_COOKIE)
        user = await state.auth.get_user(token) if token else None
        if user is None:
            return {"user": None, "auth_enabled": True}
        return {"user": {"email": user.get("email")}, "auth_enabled": True}

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

    def _require_store() -> ConversationBackend:
        if state.conversations is None:
            raise HTTPException(503, "conversation store unavailable")
        return state.conversations

    @app.get("/api/conversations")
    async def list_conversations(request: Request) -> dict[str, Any]:
        store = state.conversations
        try:
            return {"conversations": store.list_all(_uid(request)) if store else []}
        except Exception as exc:  # degraded store → empty sidebar, not a dead app
            print(f"[cmn-ai] conversation store unavailable ({exc!r}); returning empty list")
            return {"conversations": []}

    @app.post("/api/conversations")
    async def new_conversation(request: Request) -> dict[str, Any]:
        store = _require_store()
        return {
            "id": store.create(at=datetime.now(UTC), user_id=_uid(request)),
            "title": "New chat",
        }

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
        store = _require_store()
        if not store.exists(conversation_id, _uid(request)):
            raise HTTPException(404, "conversation not found")
        return {"id": conversation_id, "messages": store.messages(conversation_id)}

    @app.patch("/api/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: str, req: RenameRequest, request: Request
    ) -> dict[str, Any]:
        store = _require_store()
        if not store.exists(conversation_id, _uid(request)):
            raise HTTPException(404, "conversation not found")
        store.rename(conversation_id, req.title)
        return {"id": conversation_id, "title": req.title}

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
        store = _require_store()
        if not store.exists(conversation_id, _uid(request)):
            raise HTTPException(404, "conversation not found")
        store.delete(conversation_id)
        return {"ok": True}

    @app.post("/api/benchmark")
    async def benchmark(req: BenchmarkRequest) -> dict[str, Any]:
        """Race the same prompt across all active agents; fastest first.

        Successful paid runs are billed like normal chats. Agents whose budget
        bucket can't afford the run are skipped instead of silently spending.
        """
        import time

        from cmn_ai.orchestrator import SYSTEM_PROMPT

        prompt = (req.prompt or "").strip() or (
            "Antworte mit genau einem kurzen Satz: Was ist ein Dirigent?"
        )
        task = Task(prompt=prompt)

        async def run_one(name: str, agent: Agent) -> dict[str, Any]:
            cost = agent.cost_per_mtok
            is_free = cost.input_eur == 0 and cost.output_eur == 0
            est = 0.0 if is_free else cost.estimate(max(1, len(prompt) // 4), 200)
            if not is_free and not state.governor.can_spend(agent.bucket, est):
                return {"agent": name, "model": agent.model, "ok": False, "error": "budget"}
            start = time.perf_counter()
            try:
                response = await agent.run(task, system=SYSTEM_PROMPT)
            except Exception as exc:
                return {
                    "agent": name,
                    "model": agent.model,
                    "ok": False,
                    "seconds": round(time.perf_counter() - start, 2),
                    "error": str(exc)[:160],
                }
            state.governor.record(
                response.bucket,
                response.agent,
                response.model,
                response.usage,
                eur=response.cost_eur,
            )
            return {
                "agent": name,
                "model": response.model,
                "ok": True,
                "seconds": round(time.perf_counter() - start, 3),
                "cost_eur": response.cost_eur,
                "tokens_out": response.usage.tokens_out,
                "answer": response.text[:200],
            }

        import asyncio

        rows = await asyncio.gather(*(run_one(n, a) for n, a in state.agents.items() if a.active))
        ranked = sorted(rows, key=lambda r: (not r["ok"], r.get("seconds", 9e9)))
        return {"prompt": prompt, "results": ranked}

    @app.post("/api/export")
    async def export_document(req: ExportRequest) -> Response:
        name = (req.filename or "cmn-ai-antwort").strip() or "cmn-ai-antwort"
        payload = _render_document(req.format, req.content, req.theme or "")
        return Response(
            content=payload,
            media_type=_MEDIA_TYPES[req.format],
            headers={"Content-Disposition": f'attachment; filename="{name}.{req.format}"'},
        )

    @app.get("/api/files/{file_id}")
    async def download_file(file_id: str) -> Response:
        item = state.files.get(file_id)
        if item is None:
            raise HTTPException(404, "Datei nicht (mehr) verfügbar — bitte neu erstellen lassen.")
        return Response(
            content=item.data,
            media_type=item.media_type,
            headers={"Content-Disposition": f'attachment; filename="{item.name}"'},
        )

    @app.get("/api/vault/status")
    async def vault_status() -> dict[str, Any]:
        return {"enabled": state.vault is not None}

    @app.post("/api/vault/upload")
    async def vault_upload(req: VaultUploadRequest) -> dict[str, Any]:
        if state.vault is None:
            raise HTTPException(400, "vault is not configured (set VAULT_HOST)")
        try:
            return state.vault.upload([n.model_dump() for n in req.notes])
        except Exception as exc:  # Pi/vault unreachable
            raise HTTPException(502, "vault upload failed — is the Pi reachable?") from exc

    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
        store = state.conversations
        uid = _uid(request)
        now = datetime.now(UTC)
        # Resolve (or open) the conversation and gather prior turns as context.
        cid: str | None = None
        history: tuple[Message, ...] = ()
        if store is not None:
            try:
                cid = (
                    req.conversation_id
                    if req.conversation_id and store.exists(req.conversation_id, uid)
                    else store.create(at=now, user_id=uid)
                )
                history = tuple(
                    Message(role=m["role"], content=m["content"])
                    for m in store.messages(cid)
                    if m["role"] in ("user", "assistant")
                )
                store.add_message(cid, "user", req.prompt, at=now, user_id=uid)
            except Exception as exc:  # persistence must never block the answer itself
                print(f"[cmn-ai] conversation store failed ({exc!r}); chatting without history")
                cid, history = None, ()

        # Compose the model prompt as stacked context sections (attachments, vault
        # notes) so no source silently evicts another. The stored user message stays
        # the original prompt; only the model sees the extra context.
        sections: list[str] = []
        if req.attachments:
            files_block = render_attachments(req.attachments)
            sections.append(
                f"The user attached these files — read them and use their content:\n\n{files_block}"
            )
        if state.vault is not None:
            hits = state.vault.search(req.prompt)
            if hits:
                notes = "\n---\n".join(f"[{h['path']}]\n{h['snippet']}" for h in hits)
                sections.append(
                    "You have access to the user's personal notes. Use them if relevant, "
                    f"and say when you do.\n\nNOTES:\n{notes}"
                )
        model_prompt = req.prompt
        if sections:
            context = "\n\n---\n".join(sections)
            model_prompt = f"{context}\n\n---\nUser message: {req.prompt}"
        images = tuple(
            (mime_for(a.name), a.data) for a in (req.attachments or []) if is_image(a.name)
        )[:_MAX_IMAGES]
        task = Task(
            prompt=model_prompt,
            history=history,
            has_attachments=bool(req.attachments),
            images=images,
        )

        async def stream() -> AsyncIterator[str]:
            if cid is not None:
                yield _sse("conversation", {"id": cid})
            # A live, route-aware "working" status the UI shows as a wait indicator
            # while the models (which we call without upstream streaming) are thinking,
            # so the user sees what is actually happening.
            # The conductor may convene a team on its own when the user is on "Auto"
            # (no explicit agent). Manual "council" always convenes; a picked agent never.
            convene_council = req.agent == "council"
            try:
                cls = state.router.classify(task)
                cap, complexity = cls.capability.value, cls.complexity.value
                if not req.agent:
                    convene_council = wants_council(req.prompt, cls, req.mode)
            except Exception:
                cap, complexity = "chat", "low"
            if convene_council:
                status_label = "Das Team berät — mehrere KIs antworten, eine führt zusammen"
            elif cap == "research":
                status_label = "Recherchiere aktuelle Quellen im Web"
            elif cap == "code":
                status_label = "Analysiere den Code"
            elif cap == "multimodal":
                status_label = "Sehe mir die Datei an"
            elif complexity == "high":
                status_label = "Denke gründlich nach"
            else:
                status_label = "cmn·ai denkt"
            yield _sse("status", {"label": status_label})
            try:
                if convene_council:
                    # Manual team runs at full strength; auto-team follows the chosen mode.
                    council_mode = "power" if req.agent == "council" else req.mode
                    decision, response = await state.orchestrator.handle_council(
                        task, mode=council_mode
                    )
                else:
                    decision, response = await state.orchestrator.handle(
                        task, agent_override=req.agent
                    )
            except Exception as exc:  # surface failures to the user instead of a dead stream
                print(f"[cmn-ai] chat failed: {exc!r}")
                detail = (
                    "The selected model is unavailable. Check that the local model "
                    "(Pi/Ollama) is reachable or the agent's API key is set."
                )
                yield _sse("error", {"message": detail})
                yield _sse("done", {"error": True})
                return
            yield _sse("route", _decision_dict(decision))
            if response is None:
                yield _sse(
                    "blocked",
                    {"reason": decision.reason, "bucket": decision.classification.bucket.value},
                )
                yield _sse("done", {"blocked": True})
                return

            # Model-authored files: extract ```cmn:file``` fences, render them to
            # real documents and announce each as a download. A failed render must
            # never kill the answer — the raw fence stays in the text instead.
            answer_text = response.text
            file_events: list[dict[str, Any]] = []
            try:
                clean_text, deliverables = extract_deliverables(answer_text)
            except Exception as exc:
                print(f"[cmn-ai] deliverable extraction failed ({exc!r})")
                clean_text, deliverables = answer_text, []
            for deliverable in deliverables:
                try:
                    data = _render_document(
                        deliverable.format, deliverable.content, deliverable.theme
                    )
                    fid = state.files.put(deliverable.name, deliverable.media_type, data)
                    file_events.append({"id": fid, "name": deliverable.name})
                except Exception as exc:
                    print(f"[cmn-ai] rendering {deliverable.name} failed ({exc!r})")
            if deliverables:
                answer_text = clean_text

            if response.thinking:
                yield _sse("thinking", {"text": response.thinking})
            for word in answer_text.split(" "):
                yield _sse("delta", {"text": word + " "})
            for fe in file_events:
                yield _sse("file", {**fe, "url": f"/api/files/{fe['id']}"})
            if store is not None and cid is not None:
                try:
                    store.add_message(
                        cid,
                        "assistant",
                        answer_text,
                        at=datetime.now(UTC),
                        agent=response.agent,
                        model=response.model,
                        cost_eur=response.cost_eur,
                        user_id=uid,
                    )
                except Exception as exc:  # the user already has the answer on screen
                    print(f"[cmn-ai] failed to persist assistant message ({exc!r})")
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


def choose_conversation_backend(
    db_path: Path,
    *,
    supabase_url: str | None,
    supabase_key: str | None,
    auth: SupabaseAuth | None,
) -> ConversationBackend:
    """Pick where conversations live.

    Supabase requires a logged-in user (``cmn_conversations.user_id`` is NOT NULL and
    RLS-scoped), so it is only used when auth is configured too. Supabase creds without
    ``SUPABASE_ANON_KEY`` would mean anonymous users writing NULL user_ids — every
    insert would fail — so that combination falls back to local SQLite, loudly.
    """
    if supabase_url and supabase_key:
        if auth is not None:
            return SupabaseConversationStore(supabase_url, supabase_key)
        print(
            "[cmn-ai] SUPABASE_URL/KEY set but SUPABASE_ANON_KEY missing → no login, "
            "no user ids. Using local SQLite for conversations instead of Supabase."
        )
    return ConversationStore(db_path)


def choose_ledger_backend(
    db_path: Path, *, stateless: bool, supabase_url: str | None, supabase_key: str | None
) -> LedgerBackend:
    """Pick where spend history lives.

    ``stateless`` hosts (Vercel: no persistent disk between invocations) need the
    ledger in Supabase, or the budget brake silently resets every cold start. Without
    Supabase creds there is no durable option — fail loudly rather than pretend the
    budget is enforced when it silently isn't.
    """
    if stateless:
        if supabase_url and supabase_key:
            return SupabaseLedger(supabase_url, supabase_key)
        raise RuntimeError(
            "storage.stateless=true needs SUPABASE_URL + SUPABASE_KEY for a durable "
            "ledger — a local SQLite ledger would silently reset every cold start."
        )
    from cmn_ai.budget.ledger import Ledger

    return Ledger(db_path)


def choose_file_backend(
    *, stateless: bool, supabase_url: str | None, supabase_key: str | None
) -> FileBackend:
    """Pick where model-authored generated files (PDF/DOCX/PPTX) live.

    ``stateless`` hosts run each request in its own process, so the in-memory
    ``FileStore`` breaks the download flow the moment the follow-up GET lands on a
    different instance — Supabase keeps the bytes reachable across instances.
    """
    if stateless:
        if supabase_url and supabase_key:
            return SupabaseFileStore(supabase_url, supabase_key)
        print(
            "[cmn-ai] storage.stateless=true but SUPABASE_URL/KEY missing — generated "
            "file downloads will fail across instances. Using in-memory store anyway."
        )
    return FileStore()


def build_state_from_settings(settings: Settings | None = None) -> AppState:
    """Wire real agents, router, governor and orchestrator from configuration."""
    from cmn_ai.agents.factory import build_agents
    from cmn_ai.keystore import bootstrap_secrets

    settings = settings or load_settings()
    # Pull model API keys from Supabase (creds via local .env) and enable keyed agents.
    bootstrap_secrets(settings)
    db_path = settings.storage.resolved_path
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    ledger = choose_ledger_backend(
        db_path,
        stateless=settings.storage.stateless,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
    )
    governor = BudgetGovernor(settings=settings.budget, ledger=ledger)
    agents = build_agents(settings, governor)
    router = build_router(settings)
    decision_log = DecisionLog(db_path)
    # Hosted (multi-user) backend if Supabase *and* auth are configured; else SQLite.
    auth = build_auth_from_env(
        fallback_url=settings.supabase_url, fallback_anon=settings.supabase_anon_key
    )
    conversations = choose_conversation_backend(
        db_path, supabase_url=supabase_url, supabase_key=supabase_key, auth=auth
    )
    files = choose_file_backend(
        stateless=settings.storage.stateless, supabase_url=supabase_url, supabase_key=supabase_key
    )
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
        auth=auth,
        vault=build_vault_client_from_env(),
        files=files,
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
