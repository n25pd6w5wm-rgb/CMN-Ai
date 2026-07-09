# CLAUDE.md — cmn-ai

Project context for Claude Code and contributors. Keep this current.

## What this is

Cost-governed **multi-AI orchestration**. A lightweight router ("the conductor")
classifies each request and sends it to the best AI under a hard budget. A free local
model (Gemma via Ollama) carries the volume; paid APIs (Claude, OpenAI, Gemini,
Perplexity) are engaged only when a task needs them and the budget allows.

## Architecture (distributed, for hosted use)

- **Render** hosts this FastAPI app (chat UI + orchestrator + login) — the primary,
  supported host (`Dockerfile` + `render.yaml`, persistent disk, Python 3.11).
- **Vercel** is a serverless **experiment**, branch `vercel/serverless-deploy` (not
  merged into `build/greenfield-mvp` — it needs Python **3.12**, pinned only on that
  branch's `.python-version`, for Vercel's `uv`-based Python builder; see
  `docs/DEPLOY.md` §4a). Since serverless has no persistent disk, that branch's
  `config/vercel.yaml` sets `storage.stateless: true`, which routes the budget ledger
  and generated-file downloads to Supabase (`SupabaseLedger`/`SupabaseFileStore`,
  tables `cmn_ledger`/`cmn_files`) instead of local SQLite/in-memory.
- **Supabase** is the account backend: login/signup, per-user conversations
  (`cmn_conversations` / `cmn_messages`), and the `api_keys` table.
- **Raspberry Pi** runs the free local model (Ollama); the app reaches it via
  `OLLAMA_HOST` over an **outbound tunnel** — Tailscale Funnel is the default in
  `scripts/start-pi.sh` (no domain needed; Cloudflare Named Tunnel needs one).
- **GitHub** holds the code; Render deploys from `build/greenfield-mvp`.

Runs in two modes automatically:
- **Open / local** (no Supabase env): no login, single user, SQLite storage. For Mac dev.
- **Hosted / multi-user** (`SUPABASE_URL` + `SUPABASE_ANON_KEY` + `SUPABASE_KEY` set):
  login wall, per-user conversations in Supabase.

## Run & develop

```bash
uv sync                          # install
uv run cmn-ai serve              # http://127.0.0.1:8000  (or: uv run cmn-ai for status)
uv run cmn-ai setup              # interactive Supabase + API-key wizard
```

**Quality gates — keep all green (they gate every change):**

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict, over src + tests
uv run pytest
```

Logic is built **test-first** (TDD); the budget governor and routing policy have full
coverage. New code lands with tests + all gates green.

## Conventions (important)

- **Never commit secrets.** `.env` is gitignored. Model API keys live in Supabase
  (`api_keys`, columns `name`/`value`), loaded at startup by `keystore.py`. Do not
  hardcode keys anywhere or paste them into files.
- **Neutral git identity** — commits use the `cmn-ai` identity, never a real name.
- **Money is serious:** `budget/pricing.py` is the single source of truth for prices;
  `price_for()` raises on an unknown model on purpose (no silent zero-cost billing).
- Commit/push only when asked. Branch: `build/greenfield-mvp`.

## Models

- Configured per agent in `config/*.yaml` (`agents.<name>.model`); prices in
  `budget/pricing.py`. **Adding/switching a model = one pricing entry + the config value.**
- Use current models. Defaults: Claude coding/general → newest Sonnet, escalate → Opus;
  OpenAI → a cheap tier (`gpt-4o-mini`); local → a Pi-friendly Gemma tag.
- ⚠️ **Verify exact model IDs** against provider docs before setting them — a wrong ID
  fails at call time (now surfaced as an error event in the chat, not a silent hang).
  Entries marked `VERIFY` in `pricing.py` are best-guesses to confirm.

## Layout

```
web/        FastAPI app (app.py), templates (index/login/landing), static (chat.css/js, PWA)
web/export.py       Markdown -> PDF/DOCX/PPTX, four visual themes (report/modern/elegant/deck)
web/deliverables.py cmn:file fence parsing (name + optional theme) -> Deliverable + FileStore
router/     RuleRouter + trained Dirigent (mlx/ollama) behind a Router protocol
agents/     Agent protocol + adapters (local, anthropic, coding+tool-loop, openai, gemini, perplexity)
orchestrator.py  routes single-agent turns; handle_council runs a team in parallel + merges
                 — council_score/wants_council decide auto-team eagerness (chat modes
                 "spar"/"power"); SYSTEM_PROMPT teaches file + theme + design guidance
budget/     pricing, governor (rolling weekly caps, buckets); ledger.py (LedgerBackend
            protocol) + SQLite Ledger (Render/local) + supabase_ledger.py (Vercel)
storage/    conversations (SQLite ConversationStore + SupabaseConversationStore behind
            ConversationBackend), decisions
web/supabase_files.py  Supabase-backed FileBackend (Vercel — see deliverables.FileBackend)
web/auth.py Supabase Auth client + session gating
config.py   layered YAML + profiles (mac/pi/render/vercel) + env overrides (OLLAMA_HOST);
            storage.stateless picks the ledger/file backend (Supabase vs local)
```

## Docs

- `docs/BENUTZERHANDBUCH.md` — end-user guide (DE)
- `docs/DEPLOY.md` — GitHub + Render + Vercel (§4a) + Supabase + Pi (no port forwarding)
- `docs/router-on-pi.md` — serving the trained router on the Pi
- `scripts/install-pi.sh` / `scripts/start-pi.sh` — one-shot Pi setup + tunnel (Tailscale
  Funnel by default; both scripts hand off cleanly instead of blocking if Tailscale isn't
  set up yet — see the scripts' own comments for the exact failure messages)
```
