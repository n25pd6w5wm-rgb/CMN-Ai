"""Tests for the Supabase auth client (mocked HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cmn_ai.web.auth import AuthError, SupabaseAuth, build_auth_from_env

_URL = "https://proj.supabase.co"
_TOKEN_EP = f"{_URL}/auth/v1/token"
_SIGNUP_EP = f"{_URL}/auth/v1/signup"
_USER_EP = f"{_URL}/auth/v1/user"


def _auth() -> SupabaseAuth:
    return SupabaseAuth(_URL, "anon-key")


@respx.mock
async def test_sign_in_returns_session() -> None:
    respx.post(_TOKEN_EP).mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok", "user": {"id": "u1", "email": "a@b.de"}}
        )
    )
    session = await _auth().sign_in("a@b.de", "pw")
    assert session["access_token"] == "tok"
    assert session["user"]["email"] == "a@b.de"


@respx.mock
async def test_sign_in_bad_credentials_raises_with_message() -> None:
    respx.post(_TOKEN_EP).mock(
        return_value=httpx.Response(400, json={"error_description": "Invalid login credentials"})
    )
    with pytest.raises(AuthError, match="Invalid login credentials"):
        await _auth().sign_in("a@b.de", "wrong")


@respx.mock
async def test_sign_up_success() -> None:
    respx.post(_SIGNUP_EP).mock(
        return_value=httpx.Response(200, json={"user": {"id": "u2", "email": "new@b.de"}})
    )
    result = await _auth().sign_up("new@b.de", "pw123456")
    assert result["user"]["id"] == "u2"


@respx.mock
async def test_get_user_valid_token() -> None:
    respx.get(_USER_EP).mock(return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.de"}))
    user = await _auth().get_user("tok")
    assert user is not None and user["id"] == "u1"


@respx.mock
async def test_get_user_invalid_token_is_none() -> None:
    respx.get(_USER_EP).mock(return_value=httpx.Response(401, json={"msg": "bad jwt"}))
    assert await _auth().get_user("bad") is None


async def test_get_user_without_token_is_none() -> None:
    assert await _auth().get_user(None) is None
    assert await _auth().get_user("") is None


def test_build_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert build_auth_from_env() is None
    monkeypatch.setenv("SUPABASE_URL", _URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    assert build_auth_from_env() is not None


def test_build_auth_falls_back_to_settings_values(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hosted deploys can ship the public Supabase values in the profile config;
    # env vars still win when both are present.
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    auth = build_auth_from_env(fallback_url=_URL, fallback_anon="anon")
    assert auth is not None
    assert build_auth_from_env(fallback_url=_URL, fallback_anon=None) is None


@respx.mock
async def test_recover_posts_to_gotrue() -> None:
    route = respx.post(f"{_URL}/auth/v1/recover").mock(return_value=httpx.Response(200, json={}))
    await _auth().recover("p@example.com", redirect_to="https://app.example/reset")
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["email"] == "p@example.com"
    assert "redirect_to=https" in str(route.calls[0].request.url)


@respx.mock
async def test_recover_swallows_provider_errors() -> None:
    # Whether the address exists must not be observable from the outside.
    respx.post(f"{_URL}/auth/v1/recover").mock(return_value=httpx.Response(429, json={}))
    await _auth().recover("p@example.com", redirect_to=None)  # must not raise


@respx.mock
async def test_update_password_uses_recovery_token() -> None:
    route = respx.put(f"{_URL}/auth/v1/user").mock(return_value=httpx.Response(200, json={}))
    await _auth().update_password("recov-token", "NewPass!234")
    assert route.called
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer recov-token"
    assert json.loads(req.content)["password"] == "NewPass!234"


@respx.mock
async def test_update_password_raises_on_failure() -> None:
    respx.put(f"{_URL}/auth/v1/user").mock(
        return_value=httpx.Response(401, json={"msg": "token expired"})
    )
    try:
        await _auth().update_password("bad", "NewPass!234")
    except AuthError:
        pass
    else:
        raise AssertionError("expected AuthError")


# ---------- per-request latency: token cache + pooled client ----------
# Every /api/* request runs get_user through the auth middleware. Without a cache
# that is one full Supabase round-trip per request — the "settings menu feels slow"
# report. A short TTL cache keeps the hot path local.


@respx.mock
async def test_get_user_caches_valid_token_within_ttl() -> None:
    route = respx.get(_USER_EP).mock(
        return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.de"})
    )
    clock = {"t": 100.0}
    auth = SupabaseAuth(_URL, "anon-key", cache_ttl=60.0, now=lambda: clock["t"])
    first = await auth.get_user("tok")
    clock["t"] += 5.0
    second = await auth.get_user("tok")
    assert first == second
    assert route.call_count == 1  # second call served from cache


@respx.mock
async def test_get_user_cache_expires_after_ttl() -> None:
    route = respx.get(_USER_EP).mock(
        return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.de"})
    )
    clock = {"t": 100.0}
    auth = SupabaseAuth(_URL, "anon-key", cache_ttl=60.0, now=lambda: clock["t"])
    await auth.get_user("tok")
    clock["t"] += 61.0
    await auth.get_user("tok")
    assert route.call_count == 2  # expired -> revalidated against Supabase


@respx.mock
async def test_get_user_does_not_cache_invalid_tokens() -> None:
    route = respx.get(_USER_EP).mock(return_value=httpx.Response(401, json={"msg": "bad"}))
    auth = SupabaseAuth(_URL, "anon-key", cache_ttl=60.0, now=lambda: 100.0)
    assert await auth.get_user("bad") is None
    assert await auth.get_user("bad") is None
    assert route.call_count == 2  # invalid tokens are never cached


@respx.mock
async def test_get_user_distinct_tokens_cached_separately() -> None:
    respx.get(_USER_EP).mock(
        side_effect=[
            httpx.Response(200, json={"id": "u1"}),
            httpx.Response(200, json={"id": "u2"}),
        ]
    )
    auth = SupabaseAuth(_URL, "anon-key", cache_ttl=60.0, now=lambda: 100.0)
    u1, u2 = await auth.get_user("t1"), await auth.get_user("t2")
    assert u1 is not None and u1["id"] == "u1"
    assert u2 is not None and u2["id"] == "u2"
    again = await auth.get_user("t1")
    assert again is not None and again["id"] == "u1"  # still cached per token
