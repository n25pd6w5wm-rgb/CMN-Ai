"""Supabase-backed authentication for the web app.

The account backend is Supabase Auth (GoTrue). This thin async client signs users up,
signs them in (returning an access token), and resolves a token back to a user. The web
layer stores the access token in an http-only cookie and validates it per request.

Auth is *optional*: if ``SUPABASE_URL`` / ``SUPABASE_ANON_KEY`` are not configured the app
runs in open single-user mode (no login wall), which keeps local development and the test
suite simple. With them set, the login page gates the whole app.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class AuthError(Exception):
    """A sign-in / sign-up failed; the message is safe to show the user."""


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return "Authentication failed."
    for key in ("msg", "error_description", "error", "message"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value:
            return value
    return "Authentication failed."


class SupabaseAuth:
    """Minimal async client for Supabase Auth (GoTrue)."""

    def __init__(self, url: str, anon_key: str, *, timeout: float = 15.0) -> None:
        self._base = url.rstrip("/") + "/auth/v1"
        self._anon = anon_key
        self._timeout = timeout

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {"apikey": self._anon, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def sign_in(self, email: str, password: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/token?grant_type=password",
                headers=self._headers(),
                json={"email": email, "password": password},
            )
        if resp.status_code != 200:
            raise AuthError(_error_message(resp))
        return dict(resp.json())

    async def sign_up(self, email: str, password: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/signup",
                headers=self._headers(),
                json={"email": email, "password": password},
            )
        if resp.status_code not in (200, 201):
            raise AuthError(_error_message(resp))
        return dict(resp.json())

    async def get_user(self, token: str | None) -> dict[str, Any] | None:
        """Resolve an access token to its user, or None if missing/invalid."""
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base}/user", headers=self._headers(token))
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        return dict(resp.json())


def build_auth_from_env(
    *, fallback_url: str | None = None, fallback_anon: str | None = None
) -> SupabaseAuth | None:
    """Construct the auth client if Supabase is configured, else None (open mode).

    Env vars win; the fallbacks let hosted profiles ship the public client values
    in config so the login wall needs no manual dashboard step.
    """
    url = os.environ.get("SUPABASE_URL") or fallback_url
    anon = os.environ.get("SUPABASE_ANON_KEY") or fallback_anon
    if url and anon:
        return SupabaseAuth(url, anon)
    return None
