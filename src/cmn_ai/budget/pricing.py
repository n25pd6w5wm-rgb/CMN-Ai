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
    # Anthropic
    "claude-opus-4-8": CostPerMTok(13.8, 69.0),
    "claude-sonnet-5": CostPerMTok(3.0, 15.0),  # VERIFY exact model id + price vs Anthropic docs
    "claude-sonnet-4-6": CostPerMTok(2.8, 13.8),
    "claude-haiku-4-5": CostPerMTok(0.9, 4.6),
    # OpenAI
    "gpt-4o": CostPerMTok(2.3, 9.2),
    "gpt-4o-mini": CostPerMTok(0.14, 0.55),
    # Google
    "gemini-1.5-pro": CostPerMTok(1.15, 4.6),
    "gemini-1.5-flash": CostPerMTok(0.07, 0.28),
    # Perplexity
    "sonar-pro": CostPerMTok(2.8, 13.8),
    "sonar": CostPerMTok(0.9, 0.9),
    # Local (Ollama / Gemma) — always free
    "gemma4:latest": _FREE,
    "gemma4:31b": _FREE,
}


def price_for(model: str) -> CostPerMTok:
    """Return the price for a model, raising ``KeyError`` if it is not registered.

    Failing loudly is intentional: an unpriced paid model must never silently bill
    at zero and slip past the budget governor.
    """
    return PRICING[model]
