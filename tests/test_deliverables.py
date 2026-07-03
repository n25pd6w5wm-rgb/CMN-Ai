"""Tests for model-authored deliverables: ```cmn:file``` fences become downloads."""

from __future__ import annotations

from cmn_ai.web.deliverables import Deliverable, FileStore, extract_deliverables

_ANSWER = (
    "Hier ist dein Bericht.\n"
    "\n"
    '```cmn:file name="bericht.pdf"\n'
    "# Quartalsbericht\n"
    "\n"
    "Alles im grünen Bereich.\n"
    "```\n"
    "\n"
    "Sag Bescheid, wenn du Änderungen willst.\n"
)


def test_extracts_single_file_and_cleans_text() -> None:
    clean, files = extract_deliverables(_ANSWER)
    assert len(files) == 1
    d = files[0]
    assert d.name == "bericht.pdf"
    assert d.format == "pdf"
    assert "# Quartalsbericht" in d.content
    assert "cmn:file" not in clean
    assert "bericht.pdf" in clean  # placeholder announces the file
    assert "Sag Bescheid" in clean  # surrounding text preserved


def test_multiple_files() -> None:
    answer = (
        '```cmn:file name="a.pdf"\nInhalt A\n```\n'
        "dazwischen\n"
        '```cmn:file name="b.pptx"\n# Deck B\n```\n'
    )
    clean, files = extract_deliverables(answer)
    assert [f.name for f in files] == ["a.pdf", "b.pptx"]
    assert [f.format for f in files] == ["pdf", "pptx"]
    assert "dazwischen" in clean


def test_malformed_marker_left_untouched() -> None:
    answer = "```cmn:file ohne-name-attribut\ninhalt\n```"
    clean, files = extract_deliverables(answer)
    assert files == []
    assert clean == answer  # never destroy content


def test_unknown_extension_falls_back_to_markdown() -> None:
    _clean, files = extract_deliverables('```cmn:file name="notizen.foo"\nx\n```')
    assert files[0].format == "md"
    assert files[0].name == "notizen.foo"


def test_docx_format_inferred() -> None:
    _clean, files = extract_deliverables('```cmn:file name="brief.docx"\nHallo\n```')
    assert files[0].format == "docx"


def test_plain_answer_passes_through() -> None:
    clean, files = extract_deliverables("Nur Text, keine Datei.")
    assert files == []
    assert clean == "Nur Text, keine Datei."


# ---------- FileStore: short-lived in-memory downloads ----------


def test_store_roundtrip() -> None:
    store = FileStore(now=lambda: 100.0)
    fid = store.put("bericht.pdf", "application/pdf", b"%PDF-fake")
    item = store.get(fid)
    assert item is not None
    assert item.name == "bericht.pdf"
    assert item.data == b"%PDF-fake"


def test_store_expires_after_ttl() -> None:
    clock = {"t": 100.0}
    store = FileStore(now=lambda: clock["t"])
    fid = store.put("a.pdf", "application/pdf", b"x")
    clock["t"] += 3601.0
    assert store.get(fid) is None


def test_store_evicts_oldest_when_over_cap() -> None:
    clock = {"t": 100.0}
    store = FileStore(now=lambda: clock["t"], max_total_bytes=10)
    first = store.put("a.pdf", "application/pdf", b"123456")
    clock["t"] += 1.0
    second = store.put("b.pdf", "application/pdf", b"654321")
    assert store.get(first) is None  # evicted to make room
    assert store.get(second) is not None


def test_store_unknown_id_returns_none() -> None:
    store = FileStore(now=lambda: 0.0)
    assert store.get("nope") is None


def test_deliverable_media_type() -> None:
    assert Deliverable(name="a.pdf", format="pdf", content="").media_type == "application/pdf"
    assert "presentationml" in Deliverable(name="a.pptx", format="pptx", content="").media_type
    assert "wordprocessingml" in Deliverable(name="a.docx", format="docx", content="").media_type
    assert Deliverable(name="a.foo", format="md", content="").media_type == "text/markdown"
