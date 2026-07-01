"""Tests for the app-side vault client (mocked HTTP to the Pi vault service)."""

from __future__ import annotations

import httpx
import respx

from cmn_ai.web.vault_client import VaultClient, build_vault_client_from_env

_HOST = "https://vault.example"


@respx.mock
def test_search_returns_results() -> None:
    respx.get(f"{_HOST}/search").mock(
        return_value=httpx.Response(200, json={"results": [{"path": "a.md", "snippet": "x"}]})
    )
    results = VaultClient(_HOST).search("budget")
    assert results[0]["path"] == "a.md"


@respx.mock
def test_search_swallows_errors() -> None:
    respx.get(f"{_HOST}/search").mock(return_value=httpx.Response(500))
    assert VaultClient(_HOST).search("x") == []  # vault down → no context, no crash


@respx.mock
def test_upload_posts_notes() -> None:
    route = respx.post(f"{_HOST}/upload").mock(
        return_value=httpx.Response(200, json={"saved": 1, "total_notes": 1})
    )
    out = VaultClient(_HOST).upload([{"path": "n.md", "content": "hi"}])
    assert out["saved"] == 1 and route.called


def test_build_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("VAULT_HOST", raising=False)
    assert build_vault_client_from_env() is None
    monkeypatch.setenv("VAULT_HOST", _HOST)
    assert build_vault_client_from_env() is not None
