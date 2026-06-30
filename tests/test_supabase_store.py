"""Tests for the Supabase-backed conversation store (mocked PostgREST)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import respx

from cmn_ai.storage.supabase_store import SupabaseConversationStore

_URL = "https://proj.supabase.co"
_REST = f"{_URL}/rest/v1"
_CONV = f"{_REST}/cmn_conversations"
_MSG = f"{_REST}/cmn_messages"


def _store() -> SupabaseConversationStore:
    return SupabaseConversationStore(_URL, "service-key")


def _now() -> datetime:
    return datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


@respx.mock
def test_create_returns_uuid_and_sends_user_id() -> None:
    route = respx.post(_CONV).mock(
        return_value=httpx.Response(201, json=[{"id": "c-uuid-1", "title": "New chat"}])
    )
    cid = _store().create(at=_now(), user_id="u1")
    assert cid == "c-uuid-1"
    assert json.loads(route.calls.last.request.content)["user_id"] == "u1"


@respx.mock
def test_list_all_scopes_to_user_and_strings_ids() -> None:
    route = respx.get(_CONV).mock(
        return_value=httpx.Response(
            200, json=[{"id": "c1", "title": "Hi", "created_at": "t", "updated_at": "t"}]
        )
    )
    rows = _store().list_all(user_id="u1")
    assert rows[0]["id"] == "c1"
    assert rows[0]["message_count"] == 0
    assert "user_id=eq.u1" in str(route.calls.last.request.url)


@respx.mock
def test_exists_true_and_false() -> None:
    respx.get(_CONV).mock(return_value=httpx.Response(200, json=[{"id": "c1"}]))
    assert _store().exists("c1", user_id="u1") is True
    respx.get(_CONV).mock(return_value=httpx.Response(200, json=[]))
    assert _store().exists("missing", user_id="u1") is False


@respx.mock
def test_messages_returns_rows() -> None:
    respx.get(_MSG).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"role": "user", "content": "hi", "agent": None, "model": None, "cost_eur": 0},
                {
                    "role": "assistant",
                    "content": "yo",
                    "agent": "local",
                    "model": "m",
                    "cost_eur": 0,
                },
            ],
        )
    )
    msgs = _store().messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]


@respx.mock
def test_add_user_message_titles_untitled_conversation() -> None:
    post = respx.post(_MSG).mock(return_value=httpx.Response(201, json=[{}]))
    respx.get(_CONV).mock(return_value=httpx.Response(200, json=[{"title": "New chat"}]))
    patch = respx.patch(_CONV).mock(return_value=httpx.Response(204))

    _store().add_message("c1", "user", "Explain recursion please", at=_now(), user_id="u1")

    assert post.called
    body = json.loads(patch.calls.last.request.content)
    assert body["title"] == "Explain recursion please"  # auto-titled from first user message


@respx.mock
def test_delete_removes_messages_then_conversation() -> None:
    msg_del = respx.delete(_MSG).mock(return_value=httpx.Response(204))
    conv_del = respx.delete(_CONV).mock(return_value=httpx.Response(204))
    _store().delete("c1")
    assert msg_del.called and conv_del.called
