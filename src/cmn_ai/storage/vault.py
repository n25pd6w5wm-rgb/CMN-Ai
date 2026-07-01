"""Local markdown vault store — the user's notes, kept on the Pi (never the cloud).

The vault (an Obsidian-style folder of .md notes) lives on the Raspberry Pi. A small
service (``cmn-ai vault-serve``) exposes read-only search + upload over the same kind of
outbound tunnel used for Ollama, so the hosted app can pull relevant notes as context
without the notes ever being stored in Supabase/Render. Every path is confined to the
vault root and restricted to ``.md`` (no escape via ``..`` or symlink).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_NOTE_BYTES = 1_000_000
_SNIPPET = 240
_SKIP_DIRS = frozenset({".git", ".obsidian", ".trash", "node_modules"})


class VaultError(ValueError):
    """Invalid note path or oversized content."""


class VaultStore:
    """Markdown notes on local disk, with keyword search."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel_path: str) -> Path:
        if not rel_path.endswith(".md"):
            raise VaultError("only .md notes are allowed")
        candidate = (self.root / rel_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise VaultError(f"path '{rel_path}' escapes the vault")
        return candidate

    def save_note(self, rel_path: str, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
            raise VaultError("note exceeds size limit")
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _notes(self) -> list[Path]:
        return [
            p
            for p in self.root.rglob("*.md")
            if p.is_file()
            and not any(part in _SKIP_DIRS for part in p.relative_to(self.root).parts)
        ]

    def list_notes(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in self._notes())

    def count(self) -> int:
        return len(self._notes())

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Keyword search: notes ranked by match count, each with a short snippet."""
        needle = query.lower().strip()
        if not needle:
            return []
        hits: list[dict[str, Any]] = []
        for path in self._notes():
            try:
                text = path.read_text("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            score = text.lower().count(needle)
            if not score:
                continue
            idx = text.lower().find(needle)
            start = max(0, idx - 80)
            hits.append(
                {
                    "path": str(path.relative_to(self.root)),
                    "score": score,
                    "snippet": text[start : start + _SNIPPET].strip(),
                }
            )
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]
