"""Tests for the Supabase auth client (mocked HTTP)."""

from __future__ import annotations

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
