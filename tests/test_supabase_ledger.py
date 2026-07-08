"""Tests for the Supabase-backed spend ledger (mocked PostgREST).

Vercel's serverless functions have no persistent disk — the SQLite ledger the
budget governor relies on would reset every cold start, silently disabling the
budget brake. This backend gives the governor durable spend history via Supabase
instead, using the exact same interface as the local ``Ledger``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import respx

from cmn_ai.budget.supabase_ledger import SupabaseLedger
from cmn_ai.core import Bucket, Usage

_URL = "https://proj.supabase.co"
_REST = f"{_URL}/rest/v1"
_LEDGER = f"{_REST}/cmn_ledger"


def _ledger() -> SupabaseLedger:
    return SupabaseLedger(_URL, "service-key")


@respx.mock
def test_record_posts_a_spend_row() -> None:
    route = respx.post(_LEDGER).mock(return_value=httpx.Response(201, json=[{"id": 1}]))
    at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    _ledger().record(
        bucket=Bucket.GENERAL,
        agent="anthropic",
        model="claude",
        usage=Usage(1000, 500),
        eur=0.12,
        at=at,
    )
    body = json.loads(route.calls.last.request.content)
    assert body["bucket"] == "general"
    assert body["agent"] == "anthropic"
    assert body["tokens_in"] == 1000
    assert body["tokens_out"] == 500
    assert body["eur"] == 0.12
    assert body["ts"] == at.isoformat()


@respx.mock
def test_spent_since_sums_matching_rows() -> None:
    route = respx.get(_LEDGER).mock(
        return_value=httpx.Response(200, json=[{"eur": 0.5}, {"eur": 0.25}])
    )
    since = datetime(2026, 7, 1, tzinfo=UTC)
    total = _ledger().spent_since(Bucket.CODING, since)
    assert total == 0.75
    url = str(route.calls.last.request.url)
    assert "bucket=eq.coding" in url
    assert "ts=gte." in url and "2026-07-01" in url


@respx.mock
def test_spent_since_empty_is_zero() -> None:
    respx.get(_LEDGER).mock(return_value=httpx.Response(200, json=[]))
    assert _ledger().spent_since(Bucket.GENERAL, datetime.now(UTC)) == 0.0
