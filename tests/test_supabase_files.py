"""Tests for the Supabase-backed generated-file store (mocked PostgREST).

Vercel's serverless functions each run in their own process with no shared
filesystem, so the in-memory ``FileStore`` breaks the "AI wrote a PDF -> download
it" flow the moment the download hits a different instance. This backend persists
the rendered bytes (base64) in Supabase instead, behind the same put/get interface.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx
import respx

from cmn_ai.web.supabase_files import SupabaseFileStore

_URL = "https://proj.supabase.co"
_REST = f"{_URL}/rest/v1"
_FILES = f"{_REST}/cmn_files"


def _store() -> SupabaseFileStore:
    return SupabaseFileStore(_URL, "service-key", now=lambda: datetime(2026, 7, 1, tzinfo=UTC))


@respx.mock
def test_put_posts_base64_encoded_bytes_and_returns_id() -> None:
    del_route = respx.delete(_FILES).mock(return_value=httpx.Response(204))
    post_route = respx.post(_FILES).mock(
        return_value=httpx.Response(201, json=[{"id": "file-uuid-1"}])
    )
    fid = _store().put("bericht.pdf", "application/pdf", b"%PDF-fake")
    assert fid == "file-uuid-1"
    assert del_route.called  # purges expired rows before inserting
    body = json.loads(post_route.calls.last.request.content)
    assert body["name"] == "bericht.pdf"
    assert body["media_type"] == "application/pdf"
    assert base64.b64decode(body["data"]) == b"%PDF-fake"


@respx.mock
def test_get_decodes_stored_bytes() -> None:
    encoded = base64.b64encode(b"hello pdf").decode("ascii")
    respx.get(_FILES).mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "a.pdf", "media_type": "application/pdf", "data": encoded}],
        )
    )
    item = _store().get("file-uuid-1")
    assert item is not None
    assert item.name == "a.pdf"
    assert item.data == b"hello pdf"


@respx.mock
def test_get_missing_returns_none() -> None:
    respx.get(_FILES).mock(return_value=httpx.Response(200, json=[]))
    assert _store().get("nope") is None
