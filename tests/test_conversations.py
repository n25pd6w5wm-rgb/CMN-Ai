"""Tests for the persistent conversation store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cmn_ai.storage.conversations import ConversationStore


def _at(minute: int = 0) -> datetime:
    return datetime(2026, 6, 29, 12, minute, tzinfo=UTC)


def test_create_and_list(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["title"] == "New chat"
    assert rows[0]["message_count"] == 0


def test_first_user_message_sets_title(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    store.add_message(cid, "user", "How do I deploy this to a Raspberry Pi?", at=_at(1))
    assert store.list_all()[0]["title"] == "How do I deploy this to a Raspberry Pi?"
    # A later user message does not overwrite the established title.
    store.add_message(
        cid, "assistant", "Use the export script.", at=_at(2), agent="local", model="m"
    )
    store.add_message(cid, "user", "thanks", at=_at(3))
    assert store.list_all()[0]["title"] == "How do I deploy this to a Raspberry Pi?"


def test_long_title_is_truncated(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    store.add_message(cid, "user", "x" * 100, at=_at(1))
    title = store.list_all()[0]["title"]
    assert title.endswith("…")
    assert len(title) <= 49


def test_messages_round_trip_with_route_meta(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    store.add_message(cid, "user", "hi", at=_at(1))
    store.add_message(
        cid,
        "assistant",
        "hello",
        at=_at(2),
        agent="coding",
        model="claude-sonnet-4-6",
        cost_eur=0.04,
    )
    msgs = store.messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["agent"] == "coding"
    assert msgs[1]["cost_eur"] == 0.04
    assert store.list_all()[0]["message_count"] == 2


def test_list_orders_by_most_recently_active(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    a = store.create(at=_at(0))
    b = store.create(at=_at(1))
    # Activity on `a` after `b` was created should float `a` to the top.
    store.add_message(a, "user", "later", at=_at(5))
    assert [r["id"] for r in store.list_all()] == [a, b]


def test_rename_and_exists(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    store.rename(cid, "Pi deployment notes")
    assert store.list_all()[0]["title"] == "Pi deployment notes"
    assert store.exists(cid) is True
    assert store.exists("9999") is False


def test_delete_removes_conversation_and_messages(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    store.add_message(cid, "user", "hi", at=_at(1))
    store.delete(cid)
    assert store.list_all() == []
    assert store.messages(cid) == []
    assert store.exists(cid) is False


def test_persists_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    store = ConversationStore(db)
    cid = store.create(at=_at())
    store.add_message(cid, "user", "remember me", at=_at(1))
    reopened = ConversationStore(db)
    assert reopened.list_all()[0]["id"] == cid
    assert reopened.messages(cid)[0]["content"] == "remember me"


def test_empty_user_message_keeps_default_title(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at())
    store.add_message(cid, "user", "   ", at=_at(1))
    assert store.list_all()[0]["title"] == "New chat"


def test_updated_at_advances_with_activity(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    cid = store.create(at=_at(0))
    before = store.list_all()[0]["updated_at"]
    store.add_message(cid, "user", "hi", at=_at(0) + timedelta(minutes=10))
    after = store.list_all()[0]["updated_at"]
    assert after > before


def test_user_scoping_isolates_conversations(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c.db")
    a = store.create(at=_at(0), user_id="u1")
    store.create(at=_at(1), user_id="u2")
    assert [r["id"] for r in store.list_all(user_id="u1")] == [a]
    assert store.exists(a, user_id="u1") is True
    assert store.exists(a, user_id="u2") is False
    # open mode (no user) still sees everything
    assert len(store.list_all()) == 2
