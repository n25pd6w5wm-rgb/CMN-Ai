"""Read-only workspace tools for the coding agent's agentic loop.

The coding agent may inspect a project to answer a question, but only within a single
confined root directory and only by *reading* — no writes, no shell. Every path is
resolved and checked to stay inside the root, so the model can't escape via ``..`` or a
symlink. This is the deliberately safe first tool surface (the user chose read-only);
richer tools can be added later behind the same ``dispatch`` without touching the loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 100_000
MAX_MATCHES = 200
_SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)

# Anthropic tool schemas advertised to the model. Kept in sync with WorkspaceTools.dispatch.
WORKSPACE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace (returns up to ~100 KB).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the entries of a directory in the workspace (directories end '/').",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory relative to the root; defaults to the root.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "search",
        "description": "Case-insensitive substring search over text files; returns 'path:line: …'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring to look for."},
                "path": {
                    "type": "string",
                    "description": "Subdirectory to search; defaults to the root.",
                },
            },
            "required": ["query"],
        },
    },
]


class WorkspaceError(ValueError):
    """Raised when a tool call references a path outside the workspace or an invalid target."""


class WorkspaceTools:
    """Read-only file tools confined to ``root``. Dispatch never raises."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _resolve(self, rel: str) -> Path:
        """Resolve ``rel`` under the root, rejecting anything that escapes it."""
        candidate = (self.root / rel).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError(f"path '{rel}' escapes the workspace")
        return candidate

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        return target.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace")

    def list_dir(self, path: str = ".") -> str:
        target = self._resolve(path)
        if not target.is_dir():
            raise WorkspaceError(f"not a directory: {path}")
        lines = [
            f"{child.name}/" if child.is_dir() else child.name
            for child in sorted(target.iterdir(), key=lambda c: c.name)
            if child.name not in _SKIP_DIRS
        ]
        return "\n".join(lines) if lines else "(empty)"

    def search(self, query: str, path: str = ".") -> str:
        base = self._resolve(path)
        needle = query.lower()
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[str] = []
        for file in candidates:
            rel = file.relative_to(self.root)
            if any(part in _SKIP_DIRS for part in rel.parts) or not file.is_file():
                continue
            try:
                text = file.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # skip binary / unreadable files
            for lineno, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    matches.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                    if len(matches) >= MAX_MATCHES:
                        return "\n".join(matches) + "\n… (truncated)"
        return "\n".join(matches) if matches else "(no matches)"

    def dispatch(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool by name; return ``(text, is_error)`` and never raise.

        Errors are returned as the tool result with ``is_error=True`` so the model can
        see what went wrong and recover, rather than crashing the request.
        """
        try:
            if name == "read_file":
                return self.read_file(str(arguments["path"])), False
            if name == "list_dir":
                return self.list_dir(str(arguments.get("path", "."))), False
            if name == "search":
                return self.search(str(arguments["query"]), str(arguments.get("path", "."))), False
            return f"unknown tool: {name}", True
        except (WorkspaceError, KeyError, OSError) as exc:
            return f"error: {exc}", True
