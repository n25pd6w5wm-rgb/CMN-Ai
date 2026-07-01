"""Tests for the on-Pi markdown vault store + its HTTP service."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cmn_ai.storage.vault import VaultError, VaultStore
from cmn_ai.web.vault_service import create_vault_app


def test_save_list_count(tmp_path: Path) -> None:
    store = VaultStore(tmp_path / "vault")
    store.save_note("Projekte/cmn-ai.md", "# CMN-AI\nRouting notes")
    store.save_note("daily/2026-07-01.md", "budget thoughts")
    assert store.count() == 2
    assert "Projekte/cmn-ai.md" in store.list_notes()


def test_search_ranks_by_matches_with_snippet(tmp_path: Path) -> None:
    store = VaultStore(tmp_path / "vault")
    store.save_note("a.md", "budget budget budget governor")
    store.save_note("b.md", "one budget mention")
    results = store.search("budget")
    assert results[0]["path"] == "a.md"  # more matches ranks first
    assert results[0]["score"] == 3
    assert "budget" in results[0]["snippet"].lower()


def test_non_md_and_escape_are_rejected(tmp_path: Path) -> None:
    store = VaultStore(tmp_path / "vault")
    with pytest.raises(VaultError):
        store.save_note("notes.txt", "x")
    with pytest.raises(VaultError):
        store.save_note("../escape.md", "x")


def test_oversized_note_rejected(tmp_path: Path) -> None:
    store = VaultStore(tmp_path / "vault")
    with pytest.raises(VaultError):
        store.save_note("big.md", "x" * 2_000_000)


def test_vault_service_upload_search_health(tmp_path: Path) -> None:
    client = TestClient(create_vault_app(VaultStore(tmp_path / "vault")))
    up = client.post(
        "/upload",
        json={
            "notes": [
                {"path": "n1.md", "content": "recursion explained clearly"},
                {"path": "bad.txt", "content": "nope"},  # rejected (not .md)
            ]
        },
    ).json()
    assert up["saved"] == 1
    assert up["errors"][0]["path"] == "bad.txt"

    assert client.get("/health").json()["notes"] == 1
    results = client.get("/search", params={"q": "recursion"}).json()["results"]
    assert results and results[0]["path"] == "n1.md"
