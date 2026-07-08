"""Supabase-backed generated-file store — downloads that survive across instances.

The in-memory ``FileStore`` (see ``deliverables.py``) works fine on a single
long-lived process (Render), but breaks on serverless (Vercel): each invocation
gets its own process, so a PDF rendered while answering a chat turn is gone by the
time the browser's follow-up ``GET /api/files/{id}`` lands on a different instance.
This backend persists the rendered bytes (base64-encoded) in the Supabase Postgres
table ``cmn_files`` via PostgREST, behind the exact same ``put``/``get`` interface.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from cmn_ai.web.deliverables import StoredFile

_TTL_SECONDS = 3600.0


class SupabaseFileStore:
    """Generated files (uuid ids, TTL-purged) backed by Supabase Postgres."""

    def __init__(
        self,
        url: str,
        service_key: str,
        *,
        timeout: float = 15.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: float = _TTL_SECONDS,
    ) -> None:
        self._rest = url.rstrip("/") + "/rest/v1"
        self._key = service_key
        self._timeout = timeout
        self._now = now
        self._ttl = ttl_seconds

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _cutoff_iso(self) -> str:
        return (self._now() - timedelta(seconds=self._ttl)).isoformat()

    def _purge_expired(self) -> None:
        httpx.delete(
            f"{self._rest}/cmn_files",
            headers=self._headers(),
            params={"created_at": f"lt.{self._cutoff_iso()}"},
            timeout=self._timeout,
        )  # best-effort: an old row lingering a little longer is harmless

    def put(self, name: str, media_type: str, data: bytes) -> str:
        self._purge_expired()
        fid = uuid.uuid4().hex
        resp = httpx.post(
            f"{self._rest}/cmn_files",
            headers=self._headers("return=representation"),
            json={
                "id": fid,
                "name": name,
                "media_type": media_type,
                "data": base64.b64encode(data).decode("ascii"),
                "created_at": self._now().isoformat(),
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        return str(rows[0]["id"]) if rows else fid

    def get(self, file_id: str) -> StoredFile | None:
        resp = httpx.get(
            f"{self._rest}/cmn_files",
            headers=self._headers(),
            params={
                "id": f"eq.{file_id}",
                "created_at": f"gte.{self._cutoff_iso()}",
                "select": "name,media_type,data,created_at",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        return StoredFile(
            name=row["name"],
            media_type=row["media_type"],
            data=base64.b64decode(row["data"]),
            created_at=self._now().timestamp(),
        )
