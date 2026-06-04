"""Tests for the Supabase-backed key store and .env bootstrap."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from cmn_ai.config import load_settings
from cmn_ai.keystore import (
    apply_to_env,
    bootstrap_secrets,
    load_dotenv,
    load_supabase_keys,
    table_exists,
    update_dotenv,
    upsert_keys,
)

_TABLE_URL = "https://demo.supabase.co/rest/v1/api_keys"


@respx.mock
def test_load_supabase_keys_parses_rows() -> None:
    respx.get(_TABLE_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "ANTHROPIC_API_KEY", "value": "sk-ant-1"},
                {"name": "PERPLEXITY_API_KEY", "value": "pplx-2"},
            ],
        )
    )
    keys = load_supabase_keys(url="https://demo.supabase.co", service_key="svc")
    assert keys == {"ANTHROPIC_API_KEY": "sk-ant-1", "PERPLEXITY_API_KEY": "pplx-2"}


@respx.mock
def test_load_supabase_keys_sends_auth_headers() -> None:
    captured: dict[str, object] = {}

    def _cap(request: httpx.Request) -> httpx.Response:
        captured["apikey"] = request.headers.get("apikey")
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    respx.get(_TABLE_URL).mock(side_effect=_cap)
    load_supabase_keys(url="https://demo.supabase.co", service_key="svc")
    assert captured["apikey"] == "svc"
    assert captured["auth"] == "Bearer svc"


def test_apply_to_env_does_not_overwrite_existing() -> None:
    env: dict[str, str] = {"ANTHROPIC_API_KEY": "local-wins"}
    apply_to_env({"ANTHROPIC_API_KEY": "remote", "OPENAI_API_KEY": "remote-2"}, env=env)
    assert env["ANTHROPIC_API_KEY"] == "local-wins"
    assert env["OPENAI_API_KEY"] == "remote-2"


def test_load_dotenv_parses_file(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text('# comment\nSUPABASE_URL="https://x.supabase.co"\nSUPABASE_KEY=svc-key\n\n')
    env: dict[str, str] = {}
    load_dotenv(p, env=env)
    assert env["SUPABASE_URL"] == "https://x.supabase.co"
    assert env["SUPABASE_KEY"] == "svc-key"


@respx.mock
def test_bootstrap_loads_keys_and_enables_agents() -> None:
    respx.get(_TABLE_URL).mock(
        return_value=httpx.Response(200, json=[{"name": "ANTHROPIC_API_KEY", "value": "sk-ant-1"}])
    )
    env = {"SUPABASE_URL": "https://demo.supabase.co", "SUPABASE_KEY": "svc"}
    settings = load_settings(profile="mac")
    assert not settings.agents["anthropic"].enabled

    bootstrap_secrets(settings, env=env)

    # snapshot flags into plain bools so assertions read the post-mutation state
    enabled = {name: cfg.enabled for name, cfg in settings.agents.items()}
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-1"
    # agents whose key is now present get auto-enabled
    assert enabled["anthropic"] is True
    assert enabled["coding"] is True
    # agents without a key stay as configured
    assert enabled["openai"] is False


def test_bootstrap_without_supabase_is_noop() -> None:
    env: dict[str, str] = {}
    settings = load_settings(profile="mac")
    bootstrap_secrets(settings, env=env)  # no SUPABASE_* -> nothing happens
    assert settings.agents["anthropic"].enabled is False


@respx.mock
def test_bootstrap_survives_supabase_error(tmp_path: Path) -> None:
    respx.get(_TABLE_URL).mock(return_value=httpx.Response(500))
    env = {"SUPABASE_URL": "https://demo.supabase.co", "SUPABASE_KEY": "svc"}
    settings = load_settings(profile="mac")
    # must not raise — local-only operation should still be possible
    bootstrap_secrets(settings, env=env)
    assert settings.agents["local"].enabled is True


@respx.mock
def test_table_exists_true_on_200() -> None:
    respx.get(_TABLE_URL).mock(return_value=httpx.Response(200, json=[]))
    assert table_exists(url="https://demo.supabase.co", service_key="svc") is True


@respx.mock
def test_table_exists_false_when_missing() -> None:
    respx.get(_TABLE_URL).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    assert table_exists(url="https://demo.supabase.co", service_key="svc") is False


@respx.mock
def test_upsert_keys_posts_merge_duplicates() -> None:
    captured: dict[str, object] = {}

    def _cap(request: httpx.Request) -> httpx.Response:
        captured["prefer"] = request.headers.get("prefer")
        captured["body"] = request.content
        return httpx.Response(201, json=[])

    respx.post(_TABLE_URL).mock(side_effect=_cap)
    upsert_keys(
        {"ANTHROPIC_API_KEY": "sk-ant-1"}, url="https://demo.supabase.co", service_key="svc"
    )
    assert captured["prefer"] == "resolution=merge-duplicates"
    assert b"ANTHROPIC_API_KEY" in captured["body"]  # type: ignore[operator]


@respx.mock
def test_upsert_keys_empty_is_noop() -> None:
    route = respx.post(_TABLE_URL).mock(return_value=httpx.Response(201, json=[]))
    upsert_keys({}, url="https://demo.supabase.co", service_key="svc")
    assert not route.called


def test_update_dotenv_creates_and_updates(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("# header\nSUPABASE_URL=old\nOTHER=keep\n")
    update_dotenv(p, {"SUPABASE_URL": "new", "SUPABASE_KEY": "svc"})
    text = p.read_text()
    assert "SUPABASE_URL=new" in text
    assert "SUPABASE_KEY=svc" in text  # appended
    assert "OTHER=keep" in text  # preserved
    assert "# header" in text  # comment preserved
    assert "SUPABASE_URL=old" not in text


def test_update_dotenv_on_missing_file(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    update_dotenv(p, {"SUPABASE_URL": "x"})
    assert p.read_text() == "SUPABASE_URL=x\n"
