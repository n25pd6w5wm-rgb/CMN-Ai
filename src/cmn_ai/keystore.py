"""Secret loading: model API keys come from Supabase, bootstrap creds from .env.

Chicken-and-egg: the Supabase project URL and service key must live locally (in a
gitignored ``.env``), and the *model* API keys (Anthropic, OpenAI, …) are then read
from a Supabase table at startup and placed into the process environment — so the
existing ``Settings.api_key_for`` logic keeps working unchanged. Agents whose key
becomes available are auto-enabled. If Supabase is unreachable, the app still starts
local-only rather than crashing.

Supabase table expected (see .env.example for the SQL):
    api_keys(name text primary key, value text)
where ``name`` is the env-var name, e.g. ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

import httpx

from cmn_ai.config import Settings

EnvMap = MutableMapping[str, str]


def load_dotenv(path: Path | str, *, env: EnvMap = os.environ) -> None:
    """Load ``KEY=VALUE`` lines from a .env file into ``env`` (existing keys win)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env.setdefault(key, value)


def load_supabase_keys(
    *,
    url: str,
    service_key: str,
    table: str = "api_keys",
    timeout: float = 10.0,
) -> dict[str, str]:
    """Fetch ``{name: value}`` API keys from a Supabase (PostgREST) table."""
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    resp = httpx.get(
        f"{url.rstrip('/')}/rest/v1/{table}",
        params={"select": "name,value"},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    return {row["name"]: row["value"] for row in resp.json()}


def apply_to_env(keys: dict[str, str], *, env: EnvMap = os.environ) -> None:
    """Place keys into ``env`` without overwriting values already set locally."""
    for name, value in keys.items():
        env.setdefault(name, value)


def bootstrap_secrets(
    settings: Settings,
    *,
    env: EnvMap = os.environ,
    dotenv_path: Path | str = ".env",
) -> None:
    """Load .env, pull model keys from Supabase, and auto-enable keyed agents.

    Never raises on Supabase failure — the app must still start local-only.
    """
    load_dotenv(dotenv_path, env=env)

    url = env.get("SUPABASE_URL")
    service_key = env.get("SUPABASE_KEY")
    if url and service_key:
        try:
            keys = load_supabase_keys(url=url, service_key=service_key)
            apply_to_env(keys, env=env)
        except Exception as exc:
            print(f"[cmn-ai] Supabase key load failed ({exc}); continuing local-only.")

    # Auto-enable any agent whose API key is now available.
    for cfg in settings.agents.values():
        if cfg.api_key_env and env.get(cfg.api_key_env):
            cfg.enabled = True
