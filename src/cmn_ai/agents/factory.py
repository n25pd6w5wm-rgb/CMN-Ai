"""Factory: build the active set of agents from settings + the pricing table.

Each enabled agent in the config is instantiated and keyed by its config name.
Cloud agents are built even without an API key (they report ``active=False`` until
one is set) so the UI can show them as available-but-dormant. Capabilities and base
URLs for the known providers live here; adding a brand-new provider means a new
adapter file plus one branch here.
"""

from __future__ import annotations

from cmn_ai.agents.anthropic import AnthropicAgent
from cmn_ai.agents.base import Agent
from cmn_ai.agents.gemini import GeminiAgent
from cmn_ai.agents.local import OllamaAgent
from cmn_ai.agents.openai_compat import OpenAICompatibleAgent
from cmn_ai.budget.pricing import price_for
from cmn_ai.config import Settings
from cmn_ai.core import Capability

# Capabilities advertised by each known config key.
_CAPABILITIES: dict[str, frozenset[Capability]] = {
    "local": frozenset({Capability.CHAT, Capability.CODE}),
    "anthropic": frozenset({Capability.CHAT, Capability.CODE}),
    "coding": frozenset({Capability.CODE}),
    "openai": frozenset({Capability.CHAT, Capability.CODE}),
    "gemini": frozenset({Capability.CHAT, Capability.MULTIMODAL}),
    "perplexity": frozenset({Capability.RESEARCH, Capability.CHAT}),
}

_OPENAI_COMPAT_BASE = {
    "openai": "https://api.openai.com/v1",
    "perplexity": "https://api.perplexity.ai",
}


def build_agents(settings: Settings) -> dict[str, Agent]:
    """Instantiate every enabled agent, keyed by its config name."""
    agents: dict[str, Agent] = {}
    for name, cfg in settings.agents.items():
        if not cfg.enabled:
            continue
        caps = _CAPABILITIES.get(name, frozenset({Capability.CHAT}))
        agent: Agent

        if name == "local":
            agent = OllamaAgent(host=settings.ollama_host, model=cfg.model)
        elif name in {"anthropic", "coding"}:
            agent = AnthropicAgent(
                api_key=settings.api_key_for(name),
                model=cfg.model,
                cost_per_mtok=price_for(cfg.model),
                capabilities=caps,
                bucket=cfg.bucket,
            )
            agent.name = name
        elif name in _OPENAI_COMPAT_BASE:
            agent = OpenAICompatibleAgent(
                name=name,
                base_url=_OPENAI_COMPAT_BASE[name],
                api_key=settings.api_key_for(name),
                model=cfg.model,
                cost_per_mtok=price_for(cfg.model),
                capabilities=caps,
                bucket=cfg.bucket,
            )
        elif name == "gemini":
            agent = GeminiAgent(
                api_key=settings.api_key_for(name),
                model=cfg.model,
                cost_per_mtok=price_for(cfg.model),
                capabilities=caps,
                bucket=cfg.bucket,
            )
        else:
            # Unknown provider key — skip rather than guess.
            continue

        agents[name] = agent
    return agents
