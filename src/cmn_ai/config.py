"""Configuration: typed settings loaded from layered YAML with mac/pi profiles.

A base ``config/default.yaml`` holds shared settings; a profile file
(``config/mac.yaml`` / ``config/pi.yaml``) is deep-merged on top. The active profile
is chosen by the ``CMN_AI_PROFILE`` env var, or auto-detected from the platform.
API keys are never stored in YAML — only the *name* of the env var that holds them,
resolved at load time.
"""

from __future__ import annotations

import os
import platform
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from cmn_ai.core import Bucket

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class OnLimit(StrEnum):
    """What to do when a budget bucket is exhausted."""

    BLOCK = "block"
    FALLBACK_FREE = "fallback_free"
    ASK = "ask"


class AgentSettings(BaseModel):
    """Per-agent configuration. ``api_key_env`` names the env var holding the key."""

    enabled: bool = True
    model: str
    bucket: Bucket = Bucket.GENERAL
    api_key_env: str | None = None
    base_url: str | None = None
    escalate_model: str | None = None  # coding: model used for hard tasks


class BudgetSettings(BaseModel):
    """Budget-Governor settings. See plan decisions D-2 and D-3."""

    monthly_budget_eur: float = 35.0
    bucket_split: dict[Bucket, float] = Field(
        default_factory=lambda: {Bucket.GENERAL: 0.6, Bucket.CODING: 0.4}
    )
    on_limit: OnLimit = OnLimit.BLOCK


class RouterSettings(BaseModel):
    """Which routing strategy and (for the trained Dirigent) which model + adapter.

    ``strategy`` selects how tasks are classified:
    - ``rule``  — heuristic RuleRouter (runs everywhere, no model needed).
    - ``mlx``   — the trained Dirigent via MLX (Apple Silicon only).
    - ``ollama``— the trained Dirigent served by Ollama as a fused GGUF model, for the
      Raspberry Pi where MLX is unavailable (see ``docs/router-on-pi.md``).
    """

    strategy: str = "rule"  # "rule" | "mlx" | "ollama"
    optimizer_model: str | None = "gemma4:latest"
    synthesize: bool = False
    base_model: str = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
    adapter_path: str = "~/.cmn-ai/router-adapter"
    ollama_model: str = "cmn-dirigent"  # Ollama model name for the fused router (Pi path)
    max_new_tokens: int = 48

    @property
    def resolved_adapter_path(self) -> Path:
        return Path(self.adapter_path).expanduser()


class StorageSettings(BaseModel):
    """Where the SQLite database lives (conversations, ledger, decision logs)."""

    db_path: str = "~/.cmn-ai/cmn.db"

    @property
    def resolved_path(self) -> Path:
        return Path(self.db_path).expanduser()


class Settings(BaseModel):
    """Top-level resolved configuration for one running instance."""

    profile: str
    ollama_host: str = "http://localhost:11434"
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    router: RouterSettings = Field(default_factory=RouterSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    agents: dict[str, AgentSettings] = Field(default_factory=dict)

    def api_key_for(self, agent_name: str) -> str | None:
        """Resolve an agent's API key from its configured env var, if any."""
        agent = self.agents.get(agent_name)
        if agent is None or agent.api_key_env is None:
            return None
        return os.environ.get(agent.api_key_env)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base``, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def detect_profile() -> str:
    """Pick a profile from the environment or the host platform."""
    env = os.environ.get("CMN_AI_PROFILE")
    if env:
        return env
    machine = platform.machine().lower()
    # Raspberry Pi reports armv7l/aarch64 on Linux; Macs report arm64/x86_64 on Darwin.
    if platform.system() == "Linux" and ("arm" in machine or "aarch64" in machine):
        return "pi"
    return "mac"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping at the top level")
    return data


def load_settings(profile: str | None = None, config_dir: Path | None = None) -> Settings:
    """Load base + profile YAML, merge, and validate into a ``Settings`` object."""
    directory = config_dir or CONFIG_DIR
    chosen = profile or detect_profile()
    base = _load_yaml(directory / "default.yaml")
    profile_data = _load_yaml(directory / f"{chosen}.yaml")
    merged = _deep_merge(base, profile_data)
    merged.setdefault("profile", chosen)
    merged["profile"] = chosen
    return Settings.model_validate(merged)
