"""Model pricing — the single source of truth for cost in EUR per million tokens.

Prices are approximate EUR conversions of public list prices and are deliberately
kept in one place so the budget governor and every agent agree on cost. Update here
when provider prices change. Local models are free.
"""

from __future__ import annotations

from cmn_ai.core import CostPerMTok

_FREE = CostPerMTok(0.0, 0.0)

# EUR per million tokens (input, output). Approximate; update as prices change.
PRICING: dict[str, CostPerMTok] = {
    # Anthropic (≈EUR of USD list prices; verified vs platform.claude.com, 2026-07)
    "claude-opus-4-8": CostPerMTok(4.6, 23.0),  # $5 / $25
    "claude-sonnet-5": CostPerMTok(2.8, 13.8),  # $3 / $15 (intro $2/$10 until 2026-08-31)
    "claude-sonnet-4-6": CostPerMTok(2.8, 13.8),  # legacy
    "claude-haiku-4-5": CostPerMTok(0.9, 4.6),  # $1 / $5
    # OpenAI
    "gpt-5.4": CostPerMTok(2.3, 13.8),  # $2.50 / $15 — mid-range
    "gpt-5.4-mini": CostPerMTok(0.69, 4.14),  # $0.75 / $4.50 — good, affordable default
    "gpt-4o": CostPerMTok(2.3, 9.2),
    "gpt-4o-mini": CostPerMTok(0.14, 0.55),
    # Google (gemini-3.5-flash / 2.5-pro prices estimated — VERIFY on ai.google.dev/pricing)
    "gemini-3.5-flash": CostPerMTok(0.28, 2.3),  # VERIFY
    "gemini-2.5-pro": CostPerMTok(1.15, 9.2),  # VERIFY
    "gemini-1.5-pro": CostPerMTok(1.15, 4.6),
    "gemini-1.5-flash": CostPerMTok(0.07, 0.28),
    # Perplexity
    "sonar-pro": CostPerMTok(2.8, 13.8),
    "sonar": CostPerMTok(0.9, 0.9),
    # Local (Ollama / Gemma) — always free; tags verified vs ollama.com/library, 2026-07
    "gemma3:1b": _FREE,
    "gemma3:4b": _FREE,
    "gemma4:e2b": _FREE,
    "gemma4:e4b": _FREE,
    "gemma4:latest": _FREE,  # = e4b
    "gemma4:31b": _FREE,
}

# The local agent serves whichever Gemma tag its Ollama host has pulled (see
# agents/local.py), so every ollama-style gemma3/gemma4 tag is free by prefix
# instead of being enumerated. Paid API ids never carry the "family:tag" colon.
_FREE_LOCAL_PREFIXES = ("gemma3:", "gemma4:")


def price_for(model: str) -> CostPerMTok:
    """Return the price for a model, raising ``KeyError`` if it is not registered.

    Failing loudly is intentional: an unpriced paid model must never silently bill
    at zero and slip past the budget governor.
    """
    try:
        return PRICING[model]
    except KeyError:
        if model.startswith(_FREE_LOCAL_PREFIXES):
            return _FREE
        raise
