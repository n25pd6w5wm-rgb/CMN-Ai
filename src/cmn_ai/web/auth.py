"""Supabase-backed authentication for the web app.

The account backend is Supabase Auth (GoTrue). This thin async client signs users up,
signs them in (returning an access token), and resolves a token back to a user. The web
layer stores the access token in an http-only cookie and validates it per request.

Auth is *optional*: if ``SUPABASE_URL`` / ``SUPABASE_ANON_KEY`` are not configured the app
runs in open single-user mode (no login wall), which keeps local development and the test
suite simple. With them set, the login page gates the whole app.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

# get_user runs in the auth middleware on every /api/* request. Re-validating the
# same session token against Supabase each time adds a full network round-trip per
# request — the UI feels sluggish. A short TTL cache keeps the hot path local; a
# revoked token lives at most this long, which matches the practical risk of the
# 14-day session cookie itself.
_USER_CACHE_TTL = 60.0
_USER_CACHE_MAX = 512  # bound memory; cleared wholesale when exceeded


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

    def __init__(
        self,
        url: str,
        anon_key: str,
        *,
        timeout: float = 15.0,
        cache_ttl: float = _USER_CACHE_TTL,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = url.rstrip("/") + "/auth/v1"
        self._anon = anon_key
        self._timeout = timeout
        # One pooled client for the app's lifetime: reusing connections skips a
        # fresh TCP+TLS handshake to Supabase on every auth check.
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._ttl = cache_ttl
        self._now = now

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {"apikey": self._anon, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def sign_in(self, email: str, password: str) -> dict[str, Any]:
        resp = await self._http().post(
            f"{self._base}/token?grant_type=password",
            headers=self._headers(),
            json={"email": email, "password": password},
        )
        if resp.status_code != 200:
            raise AuthError(_error_message(resp))
        return dict(resp.json())

    async def sign_up(self, email: str, password: str) -> dict[str, Any]:
        resp = await self._http().post(
            f"{self._base}/signup",
            headers=self._headers(),
            json={"email": email, "password": password},
        )
        if resp.status_code not in (200, 201):
            raise AuthError(_error_message(resp))
        return dict(resp.json())

    async def recover(self, email: str, *, redirect_to: str | None) -> None:
        """Ask GoTrue to send a password-reset mail.

        Never raises on provider errors: whether an address exists (or the mailer is
        rate-limited) must not be observable by the caller of our public endpoint.
        """
        params = {"redirect_to": redirect_to} if redirect_to else None
        with contextlib.suppress(httpx.HTTPError):
            await self._http().post(
                f"{self._base}/recover",
                headers=self._headers(),
                params=params,
                json={"email": email},
            )

    async def update_password(self, recovery_token: str, new_password: str) -> None:
        """Set a new password using the access token from the reset-mail link."""
        resp = await self._http().put(
            f"{self._base}/user",
            headers=self._headers(recovery_token),
            json={"password": new_password},
        )
        if resp.status_code != 200:
            raise AuthError(_error_message(resp))

    async def get_user(self, token: str | None) -> dict[str, Any] | None:
        """Resolve an access token to its user, or None if missing/invalid.

        Successful lookups are cached per token for a short TTL (see
        ``_USER_CACHE_TTL``) so the auth middleware doesn't pay a Supabase
        round-trip on every single API request. Invalid tokens are never cached.
        """
        if not token:
            return None
        cached = self._cache.get(token)
        if cached is not None and self._now() - cached[0] < self._ttl:
            return cached[1]
        try:
            resp = await self._http().get(f"{self._base}/user", headers=self._headers(token))
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            self._cache.pop(token, None)
            return None
        user = dict(resp.json())
        if len(self._cache) >= _USER_CACHE_MAX:
            self._cache.clear()
        self._cache[token] = (self._now(), user)
        return user


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
