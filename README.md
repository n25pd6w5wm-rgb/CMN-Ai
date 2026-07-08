# cmn-ai

Cost-governed multi-AI orchestration with intelligent, local-first routing.

A lightweight **router** ("the conductor") classifies every request and routes it to
the best AI under a **hard budget limit**, controlled through a local chat web UI.
A free local model (Gemma via Ollama) carries the volume; paid APIs (Claude, OpenAI,
Gemini, Perplexity) are engaged only when a task needs them and the budget allows.

> **End-user guide (German):** [`docs/BENUTZERHANDBUCH.md`](docs/BENUTZERHANDBUCH.md) —
> how to install, run, configure and what to know. This README is the developer overview.

## Architecture

```
core.py      Shared domain types (Task, Classification, AgentResponse, RouteDecision, …)
config.py    Layered YAML config + mac/pi profiles + budget/router/agent settings
agents/      Agent protocol + adapters (local, anthropic, coding, openai, gemini, perplexity)
router/      The Dirigent behind a swappable Router protocol (RuleRouter today)
budget/      Pricing table, SQLite ledger, Budget-Governor (rolling weekly caps, buckets)
storage/     SQLite decision log (routing decisions + outcomes = training data)
orchestrator.py  Per-request glue: classify → select → run → record → log
web/         FastAPI app + SSE chat UI (budget meter, route transparency, analytics)
```

### How routing works

1. `RuleRouter.classify` (free, local) reads each task: capability
   (chat / code / research / multimodal), complexity, whether it needs the web.
2. `RuleRouter.select` picks an agent under the **Budget-Governor**:
   - low-complexity chat/code → **free local Gemma**;
   - hard code → **Claude coding agent** (Sonnet, escalating to Opus) on the *coding* bucket;
   - research → **Perplexity**; multimodal → **Gemini** — when the budget permits.
3. Over budget, behaviour follows `on_limit`: a hard **block** (default) or a free fallback.
4. Every decision and its real cost is logged for a future trained router.

### Coding agent (agentic tool loop)

The Claude coding agent can run an **agentic tool loop**: given a workspace directory,
it inspects the project with read-only tools (`read_file`, `list_dir`, `search`) and,
when writing is enabled, edits it (`write_file`, `edit_file`). There is no shell. Every
path is confined to the workspace root (no `..` or symlink escape), and the loop is
bounded by a hard iteration cap plus a budget guard, so a multi-turn request can never
overspend the coding bucket. It is off by default — enable it per the coding agent:

```yaml
agents:
  coding:
    workspace_root: ~/code/my-project   # enables the read-only tool loop over this dir
    workspace_writable: false           # set true to allow write_file / edit_file (sandbox)
```

Point `workspace_root` at a working copy when enabling writes — that directory is the
sandbox. `/api/models` reports each coding agent's `tools` status (`workspace`, `writable`).

### Trained router & prompt optimisation

The Dirigent is swappable via `router.strategy`: `rule` (heuristic, default), `mlx` (the
trained LoRA model on Apple Silicon), or `ollama` (the same model on a Raspberry Pi — see
[`docs/router-on-pi.md`](docs/router-on-pi.md)). Optionally set `router.optimize: true` to
let the local Gemma rewrite substantial prompts for clarity before they are answered
(fail-safe: trivial prompts are skipped and any error falls back to the original).

### Budget governor

Monthly budget (default 35 €) is scaled to a **rolling 7-day window** and split into
isolated **buckets** (general 60% / coding 40%) so coding can never starve general use.
Paid calls are checked against the bucket's remaining headroom; free local calls always
run. `raise_budget` lifts the cap immediately (the future "buy credits" hook).

## Quick start

```bash
uv sync
# optional: enable paid agents by setting keys + enabling them in config/
export ANTHROPIC_API_KEY=...      # PERPLEXITY_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
uv run cmn-ai serve               # then open http://127.0.0.1:8000
```

Ollama must be running locally with a Gemma model pulled (e.g. `ollama pull gemma4`).
The active profile (mac/pi) is auto-detected or set via `CMN_AI_PROFILE`.

### Supabase keys (one command)

Instead of exporting keys by hand, store them once in Supabase and let the app load them
at startup (agents with a key auto-enable). The wizard writes `.env`, checks/creates the
`api_keys` table, and uploads your model keys:

```bash
uv run cmn-ai setup
```

It asks for your Supabase project URL + service-role key, then for each model key
(blank = skip). If the `api_keys` table is missing, it prints the SQL (copied to your
clipboard) and a link to the SQL editor. See `.env.example` for the manual path.

## Accounts & hosting

cmn-ai runs two ways:

- **Local / open mode** — no Supabase env set: no login wall, single user. Ideal for dev
  on the Mac (`uv run cmn-ai serve`).
- **Hosted / multi-user** — set `SUPABASE_URL` + `SUPABASE_ANON_KEY`: a login/signup page
  gates the app (Supabase Auth), conversations are per-user, and the session lives in an
  http-only cookie.

The app is an installable **PWA** (manifest + service worker) — "Add to Dock" in Safari or
the install icon in Chrome. For the full distributed setup (GitHub → host runs the app,
Supabase for accounts/keys, the Raspberry Pi runs the local model via `OLLAMA_HOST`), see
**[`docs/DEPLOY.md`](docs/DEPLOY.md)**. Two hosts are supported out of the box:
**Render** (container: `Dockerfile`; blueprint: `render.yaml`) and **Vercel** (serverless:
`api/index.py` + `vercel.json` + `requirements.txt`).

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

All logic is built test-first; the money-critical budget governor and the routing
policy have full coverage.
