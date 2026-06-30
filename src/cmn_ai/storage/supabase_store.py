"""Supabase-backed conversation store (the hosted, multi-user backend).

Mirrors ``ConversationStore``'s interface but persists to the Supabase Postgres tables
``cmn_conversations`` / ``cmn_messages`` (uuid ids, scoped to the authenticated user via
``user_id`` → ``auth.users``). It talks to Supabase's REST API (PostgREST) with the
service-role key, which bypasses RLS — so the app filters by ``user_id`` itself.

Calls are synchronous (httpx.Client) to match the SQLite store's interface; at this
product's volume the brief blocking REST call is acceptable. The local SQLite store stays
the default for open / single-user mode; this one is used when Supabase is configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

_DEFAULT_TITLE = "New chat"
_TITLE_MAX = 48


def _derive_title(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_TITLE_MAX].rstrip() + ("…" if len(flat) > _TITLE_MAX else "") or _DEFAULT_TITLE


class SupabaseConversationStore:
    """Conversations + messages in Supabase, scoped per authenticated user."""

    def __init__(self, url: str, service_key: str, *, timeout: float = 15.0) -> None:
        self._rest = url.rstrip("/") + "/rest/v1"
        self._key = service_key
        self._timeout = timeout

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def create(
        self, *, at: datetime, title: str = _DEFAULT_TITLE, user_id: str | None = None
    ) -> str:
        resp = httpx.post(
            f"{self._rest}/cmn_conversations",
            headers=self._headers("return=representation"),
            json={"user_id": user_id, "title": title},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return str(resp.json()[0]["id"])

    def list_all(self, user_id: str | None = None) -> list[dict[str, Any]]:
        params = {"select": "id,title,created_at,updated_at", "order": "updated_at.desc"}
        if user_id is not None:
            params["user_id"] = f"eq.{user_id}"
        resp = httpx.get(
            f"{self._rest}/cmn_conversations",
            headers=self._headers(),
            params=params,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        rows: list[dict[str, Any]] = resp.json()
        for row in rows:
            row["id"] = str(row["id"])
            row.setdefault("message_count", 0)
        return rows

    def exists(self, conversation_id: str, user_id: str | None = None) -> bool:
        params = {"id": f"eq.{conversation_id}", "select": "id"}
        if user_id is not None:
            params["user_id"] = f"eq.{user_id}"
        resp = httpx.get(
            f"{self._rest}/cmn_conversations",
            headers=self._headers(),
            params=params,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return bool(resp.json())

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        resp = httpx.get(
            f"{self._rest}/cmn_messages",
            headers=self._headers(),
            params={
                "conversation_id": f"eq.{conversation_id}",
                "select": "role,content,agent,model,cost_eur",
                "order": "created_at",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return list(resp.json())

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        at: datetime,
        agent: str | None = None,
        model: str | None = None,
        cost_eur: float | None = None,
        user_id: str | None = None,
    ) -> None:
        httpx.post(
            f"{self._rest}/cmn_messages",
            headers=self._headers(),
            json={
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "agent": agent,
                "model": model,
                "cost_eur": cost_eur or 0,
            },
            timeout=self._timeout,
        ).raise_for_status()
        # Bump updated_at, and title the chat after its first user message.
        patch: dict[str, Any] = {"updated_at": at.isoformat()}
        if role == "user" and self._is_untitled(conversation_id):
            patch["title"] = _derive_title(content)
        self._patch_conversation(conversation_id, patch)

    def _is_untitled(self, conversation_id: str) -> bool:
        resp = httpx.get(
            f"{self._rest}/cmn_conversations",
            headers=self._headers(),
            params={"id": f"eq.{conversation_id}", "select": "title"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        return bool(rows) and rows[0]["title"] == _DEFAULT_TITLE

    def _patch_conversation(self, conversation_id: str, body: dict[str, Any]) -> None:
        httpx.patch(
            f"{self._rest}/cmn_conversations",
            headers=self._headers(),
            params={"id": f"eq.{conversation_id}"},
            json=body,
            timeout=self._timeout,
        ).raise_for_status()

    def rename(self, conversation_id: str, title: str) -> None:
        clean = " ".join(title.split())[:_TITLE_MAX] or _DEFAULT_TITLE
        self._patch_conversation(conversation_id, {"title": clean})

    def delete(self, conversation_id: str) -> None:
        for table in ("cmn_messages", "cmn_conversations"):
            key = "conversation_id" if table == "cmn_messages" else "id"
            httpx.delete(
                f"{self._rest}/{table}",
                headers=self._headers(),
                params={key: f"eq.{conversation_id}"},
                timeout=self._timeout,
            ).raise_for_status()
