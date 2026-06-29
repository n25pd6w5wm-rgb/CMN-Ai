"""File tools for the coding agent's agentic loop, confined to one workspace root.

The agent inspects — and, when the workspace is ``writable``, edits — a project, but
only within a single confined root directory and never via a shell. Every path is
resolved and checked to stay inside the root, so the model can't escape via ``..`` or a
symlink, on reads *or* writes. Writing is opt-in (the user enables it deliberately): the
configured root is the sandbox, so point it at a working copy. ``dispatch`` never raises
— failures come back as tool results with ``is_error=True`` so the model can recover.
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
READ_TOOLS: list[dict[str, Any]] = [
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

WRITE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file in the workspace with given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root."},
                "content": {"type": "string", "description": "Full new file contents."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace one exact, unique occurrence of 'old' with 'new' in a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root."},
                "old": {"type": "string", "description": "Exact text to replace (must be unique)."},
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        },
    },
]


class WorkspaceError(ValueError):
    """Raised when a tool call references a path outside the workspace or an invalid target."""


class WorkspaceTools:
    """File tools confined to ``root``. Writes require ``writable=True``. Dispatch never raises."""

    def __init__(self, root: Path, *, writable: bool = False) -> None:
        self.root = root.resolve()
        self.writable = writable

    @property
    def tools(self) -> list[dict[str, Any]]:
        """The tool schemas to advertise: read-only, plus write tools when writable."""
        return READ_TOOLS + WRITE_TOOLS if self.writable else READ_TOOLS

    def _resolve(self, rel: str) -> Path:
        """Resolve ``rel`` under the root, rejecting anything that escapes it."""
        candidate = (self.root / rel).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError(f"path '{rel}' escapes the workspace")
        return candidate

    # -- read --------------------------------------------------------------

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

    # -- write (opt-in) ----------------------------------------------------

    def _require_writable(self) -> None:
        if not self.writable:
            raise WorkspaceError("workspace is read-only; writing is disabled")

    def write_file(self, path: str, content: str) -> str:
        self._require_writable()
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise WorkspaceError(f"content exceeds {MAX_FILE_BYTES} bytes")
        target = self._resolve(path)
        if target == self.root or target.is_dir():
            raise WorkspaceError(f"not a writable file path: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    def edit_file(self, path: str, old: str, new: str) -> str:
        self._require_writable()
        target = self._resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        text = target.read_text("utf-8")
        count = text.count(old)
        if count == 0:
            raise WorkspaceError("'old' text not found")
        if count > 1:
            raise WorkspaceError(f"'old' text is not unique ({count} occurrences)")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"edited {path}"

    # -- dispatch ----------------------------------------------------------

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
            if name == "write_file":
                return self.write_file(str(arguments["path"]), str(arguments["content"])), False
            if name == "edit_file":
                return (
                    self.edit_file(
                        str(arguments["path"]), str(arguments["old"]), str(arguments["new"])
                    ),
                    False,
                )
            return f"unknown tool: {name}", True
        except (WorkspaceError, KeyError, OSError) as exc:
            return f"error: {exc}", True
