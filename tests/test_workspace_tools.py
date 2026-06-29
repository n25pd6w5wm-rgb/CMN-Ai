"""Tests for the read-only workspace tools (security-critical: path containment)."""

from __future__ import annotations

from pathlib import Path

from cmn_ai.agents.workspace import WorkspaceTools


def _workspace(tmp_path: Path) -> WorkspaceTools:
    (tmp_path / "a.txt").write_text("hello world\nsecond line\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("def foo():\n    return 42\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hello secret")
    return WorkspaceTools(tmp_path)


def test_read_file_within_workspace(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    assert "hello world" in ws.read_file("a.txt")


def test_list_dir_marks_dirs_and_skips_noise(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    listing = ws.list_dir(".")
    assert "a.txt" in listing
    assert "sub/" in listing
    assert ".git" not in listing  # noise dirs hidden


def test_search_finds_matches_and_skips_skip_dirs(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    result = ws.search("hello")
    assert "a.txt:1:" in result
    assert ".git" not in result  # never search inside skipped dirs


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    text, is_error = ws.dispatch("read_file", {"path": "../outside.txt"})
    assert is_error is True
    assert "escapes" in text


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "link").symlink_to(secret)
    ws = WorkspaceTools(root)
    _text, is_error = ws.dispatch("read_file", {"path": "link"})
    assert is_error is True  # resolved symlink points outside the root


def test_dispatch_unknown_tool_is_error(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    text, is_error = ws.dispatch("delete_everything", {})
    assert is_error is True
    assert "unknown tool" in text


def test_dispatch_missing_file_is_error_not_raise(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _text, is_error = ws.dispatch("read_file", {"path": "nope.txt"})
    assert is_error is True


# -- write tools (opt-in) --------------------------------------------------


def test_read_only_workspace_hides_and_blocks_write_tools(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)  # writable defaults to False
    assert {t["name"] for t in ws.tools} == {"read_file", "list_dir", "search"}
    text, is_error = ws.dispatch("write_file", {"path": "x.txt", "content": "nope"})
    assert is_error is True
    assert "read-only" in text
    assert not (tmp_path / "x.txt").exists()


def test_writable_workspace_advertises_write_tools(tmp_path: Path) -> None:
    ws = WorkspaceTools(tmp_path, writable=True)
    assert {t["name"] for t in ws.tools} == {
        "read_file",
        "list_dir",
        "search",
        "write_file",
        "edit_file",
    }


def test_write_file_creates_file_within_workspace(tmp_path: Path) -> None:
    ws = WorkspaceTools(tmp_path, writable=True)
    _text, is_error = ws.dispatch("write_file", {"path": "sub/new.py", "content": "x = 1\n"})
    assert is_error is False
    assert (tmp_path / "sub" / "new.py").read_text() == "x = 1\n"


def test_write_file_escape_is_rejected(tmp_path: Path) -> None:
    ws = WorkspaceTools(tmp_path, writable=True)
    _text, is_error = ws.dispatch("write_file", {"path": "../evil.py", "content": "boom"})
    assert is_error is True
    assert not (tmp_path.parent / "evil.py").exists()


def test_edit_file_replaces_unique_occurrence(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text("value = 1\n")
    ws = WorkspaceTools(tmp_path, writable=True)
    _text, is_error = ws.dispatch("edit_file", {"path": "c.py", "old": "1", "new": "2"})
    assert is_error is False
    assert (tmp_path / "c.py").read_text() == "value = 2\n"


def test_edit_file_ambiguous_match_is_error(tmp_path: Path) -> None:
    (tmp_path / "d.py").write_text("a = 1\nb = 1\n")
    ws = WorkspaceTools(tmp_path, writable=True)
    text, is_error = ws.dispatch("edit_file", {"path": "d.py", "old": "1", "new": "2"})
    assert is_error is True
    assert "not unique" in text
    assert (tmp_path / "d.py").read_text() == "a = 1\nb = 1\n"  # unchanged


def test_edit_file_missing_string_is_error(tmp_path: Path) -> None:
    (tmp_path / "e.py").write_text("hello\n")
    ws = WorkspaceTools(tmp_path, writable=True)
    text, is_error = ws.dispatch("edit_file", {"path": "e.py", "old": "absent", "new": "x"})
    assert is_error is True
    assert "not found" in text
