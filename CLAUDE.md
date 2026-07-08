# CLAUDE.md — cmn-ai

Project context for Claude Code and contributors. Keep this current.

## What this is

Cost-governed **multi-AI orchestration**. A lightweight router ("the conductor")
classifies each request and sends it to the best AI under a hard budget. A free local
model (Gemma via Ollama) carries the volume; paid APIs (Claude, OpenAI, Gemini,
Perplexity) are engaged only when a task needs them and the budget allows.

## Architecture (distributed, for hosted use)

- **Render** (Docker: `Dockerfile` + `render.yaml`) or **Vercel** (serverless:
  `api/index.py` + `vercel.json` + `requirements.txt`) hosts this FastAPI app
  (chat UI + orchestrator + login). Same code, pick either host — see `docs/DEPLOY.md`.
- **Supabase** is the account backend: login/signup, per-user conversations
  (`cmn_conversations` / `cmn_messages`), and the `api_keys` table.
- **Raspberry Pi** runs the free local model (Ollama); the app reaches it via
  `OLLAMA_HOST` over an **outbound tunnel** (Cloudflare/Tailscale — no port forwarding).
- **GitHub** holds the code; Render/Vercel deploy from it.

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
router/     RuleRouter + trained Dirigent (mlx/ollama) behind a Router protocol
agents/     Agent protocol + adapters (local, anthropic, coding+tool-loop, openai, gemini, perplexity)
budget/     pricing, ledger, governor (rolling weekly caps, buckets)
storage/    conversations (SQLite ConversationStore + SupabaseConversationStore behind
            ConversationBackend), decisions
web/auth.py Supabase Auth client + session gating
config.py   layered YAML + profiles (mac/pi/render) + env overrides (OLLAMA_HOST)
```

## Docs

- `docs/BENUTZERHANDBUCH.md` — end-user guide (DE)
- `docs/DEPLOY.md` — GitHub + Render + Supabase + Pi (no port forwarding)
- `docs/router-on-pi.md` — serving the trained router on the Pi
- `scripts/install-pi.sh` — one-shot Pi setup (Ollama + model + tunnel)
```
