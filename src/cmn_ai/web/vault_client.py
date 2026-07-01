"""Client the hosted app uses to reach the on-Pi vault service (over a tunnel).

Configured via ``VAULT_HOST`` (the Pi's tunneled vault URL). Search failures are
swallowed — if the vault is unreachable the chat still works, just without note context.
Upload errors propagate so the UI can report them.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class VaultClient:
    def __init__(self, host: str, *, timeout: float = 15.0) -> None:
        self._base = host.rstrip("/")
        self._timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(
                f"{self._base}/search",
                params={"q": query, "limit": limit},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return list(resp.json().get("results", []))
        except httpx.HTTPError:
            return []  # vault down → answer without note context, never break the chat

    def upload(self, notes: list[dict[str, str]]) -> dict[str, Any]:
        resp = httpx.post(
            f"{self._base}/upload", json={"notes": notes}, timeout=max(self._timeout, 60.0)
        )
        resp.raise_for_status()
        return dict(resp.json())


def build_vault_client_from_env() -> VaultClient | None:
    host = os.environ.get("VAULT_HOST")
    return VaultClient(host) if host else None
