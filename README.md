# cmn-ai

Cost-governed multi-AI orchestration with intelligent, local-first routing.

A lightweight **router** ("the conductor") classifies every request and routes it to
the best AI under a **hard budget limit**, controlled through a local chat web UI.
A free local model (Gemma via Ollama) carries the volume; paid APIs (Claude, OpenAI,
Gemini, Perplexity) are engaged only when a task needs them and the budget allows.

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

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

All logic is built test-first; the money-critical budget governor and the routing
policy have full coverage.
