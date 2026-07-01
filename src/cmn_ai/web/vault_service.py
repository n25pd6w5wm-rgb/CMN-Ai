"""Tiny HTTP service for the on-Pi markdown vault (run via ``cmn-ai vault-serve``).

Exposes read-only search + JSON upload so the hosted app can pull relevant notes as
context and push uploaded notes — without the notes leaving the Pi. Upload is JSON
(``{"notes": [{"path", "content"}]}``) so no multipart dependency is needed; the browser
reads the files and the app forwards them here. Meant to sit behind an outbound tunnel.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from cmn_ai.storage.vault import VaultError, VaultStore


class _Note(BaseModel):
    path: str
    content: str


class _Upload(BaseModel):
    notes: list[_Note]


def create_vault_app(store: VaultStore) -> FastAPI:
    app = FastAPI(title="cmn-ai vault")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "notes": store.count()}

    @app.get("/search")
    async def search(q: str, limit: int = 5) -> dict[str, Any]:
        return {"results": store.search(q, limit)}

    @app.post("/upload")
    async def upload(req: _Upload) -> dict[str, Any]:
        saved = 0
        errors: list[dict[str, str]] = []
        for note in req.notes:
            try:
                store.save_note(note.path, note.content)
                saved += 1
            except VaultError as exc:
                errors.append({"path": note.path, "error": str(exc)})
        return {"saved": saved, "errors": errors, "total_notes": store.count()}

    return app


def default_vault_dir() -> Path:
    return Path(os.environ.get("CMN_AI_VAULT_DIR", "~/.cmn-ai/vault")).expanduser()


def vault_app_factory() -> FastAPI:
    """uvicorn entry point: builds the app from CMN_AI_VAULT_DIR (default ~/.cmn-ai/vault)."""
    return create_vault_app(VaultStore(default_vault_dir()))
