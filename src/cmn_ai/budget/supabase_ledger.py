"""Supabase-backed spend ledger — the durable budget record for serverless hosts.

Mirrors ``Ledger``'s interface (same ``record``/``spent_since`` signatures the
governor calls) but persists to the Supabase Postgres table ``cmn_ledger`` via
PostgREST instead of a local SQLite file. Serverless hosts (Vercel) have no
persistent disk between invocations, so the local SQLite ledger would silently
reset every cold start — quietly disabling the budget brake. This backend keeps
spend durable there; Render keeps using the local SQLite ``Ledger`` (cheaper, no
network round-trip, and it already has a persistent disk).
"""

from __future__ import annotations

from datetime import datetime

import httpx

from cmn_ai.core import Bucket, Usage


class SupabaseLedger:
    """Append-only spend log backed by Supabase Postgres (table ``cmn_ledger``)."""

    def __init__(self, url: str, service_key: str, *, timeout: float = 15.0) -> None:
        self._rest = url.rstrip("/") + "/rest/v1"
        self._key = service_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def record(
        self,
        *,
        bucket: Bucket,
        agent: str,
        model: str,
        usage: Usage,
        eur: float,
        at: datetime,
    ) -> None:
        httpx.post(
            f"{self._rest}/cmn_ledger",
            headers=self._headers(),
            json={
                "ts": at.isoformat(),
                "bucket": bucket.value,
                "agent": agent,
                "model": model,
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "eur": eur,
            },
            timeout=self._timeout,
        ).raise_for_status()

    def spent_since(self, bucket: Bucket, since: datetime) -> float:
        resp = httpx.get(
            f"{self._rest}/cmn_ledger",
            headers=self._headers(),
            params={
                "bucket": f"eq.{bucket.value}",
                "ts": f"gte.{since.isoformat()}",
                "select": "eur",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return sum(float(row["eur"]) for row in resp.json())
