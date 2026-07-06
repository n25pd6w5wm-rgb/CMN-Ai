"""Tests for document generation: Markdown answers become PDF/PPTX/DOCX files."""

from __future__ import annotations

import io

from cmn_ai.web.export import to_docx, to_pdf, to_pptx

_RICH = (
    "# Bericht\n"
    "\n"
    "Einleitung mit **wichtigen** Umlauten: äöüß und Unicode: Δx → ∞.\n"
    "\n"
    "## Zahlen\n"
    "\n"
    "| Monat | Betrag |\n"
    "| --- | --- |\n"
    "| Juli | 1234 € |\n"
    "\n"
    "### Details\n"
    "\n"
    "- Punkt eins\n"
    "- Punkt zwei\n"
    "\n"
    "```python\n"
    "print('hallo')\n"
    "```\n"
)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def test_pdf_renders_unicode_without_replacement() -> None:
    data = to_pdf("# Test\n\nGriechisch: Δ und Pfeil: →")
    assert data[:4] == b"%PDF"
    text = _pdf_text(data)
    assert "Δ" in text
    assert "→" in text
    assert "?" not in text.replace("? ", "")  # no replacement chars


def test_pdf_renders_table_and_code_without_crash() -> None:
    data = to_pdf(_RICH)
    assert data[:4] == b"%PDF"
    text = _pdf_text(data)
    assert "Juli" in text
    assert "1234" in text
    assert "print" in text


def test_pdf_heading_levels_do_not_crash() -> None:
    data = to_pdf("# a\n## b\n### c\n\ntext")
    assert data[:4] == b"%PDF"


def test_docx_roundtrip_contains_content() -> None:
    from docx import Document

    data = to_docx(_RICH)
    doc = Document(io.BytesIO(data))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    table_text = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "Bericht" in all_text
    assert "Punkt eins" in all_text
    assert "print('hallo')" in all_text
    assert "Juli" in table_text


def test_pptx_still_builds_slides() -> None:
    data = to_pptx(_RICH)
    assert data[:2] == b"PK"


_DOC = (
    "# Quartalsbericht\n\n"
    "Eine *kurze* Einleitung mit **Nachdruck** und `inline code`.\n\n"
    "## Zahlen\n\n"
    "| Monat | Umsatz |\n| --- | --- |\n| Juli | 1234 € |\n\n"
    "### Schritte\n\n"
    "1. Erster Schritt\n"
    "2. Zweiter Schritt\n"
    "   1. Unterpunkt\n\n"
    "- Ein Aufzählungspunkt\n\n"
    "> Ein wichtiges Zitat.\n\n"
    "---\n\n"
    "```python\nprint('hallo')\n```\n"
)


def test_pdf_rich_document_builds() -> None:
    data = to_pdf(_DOC)
    assert data[:4] == b"%PDF"
    text = _pdf_text(data)
    for needle in ("Quartalsbericht", "Erster Schritt", "Zweiter Schritt", "Zitat", "print"):
        assert needle in text


def test_docx_numbered_list_and_quote() -> None:
    from docx import Document

    doc = Document(io.BytesIO(to_docx(_DOC)))
    styles = [p.style.name if p.style else "" for p in doc.paragraphs]
    text = "\n".join(p.text for p in doc.paragraphs)
    assert any("Number" in s for s in styles)  # a real numbered-list style is used
    assert any(s in ("Quote", "Intense Quote") for s in styles) or "Zitat" in text
    assert "Erster Schritt" in text
    assert "Title" in styles  # first H1 becomes the document title


def test_parse_recognises_ordered_and_quote() -> None:
    from cmn_ai.web.export import _parse

    kinds = {b.kind for b in _parse(_DOC)}
    assert {"number", "bullet", "quote", "hr", "code", "table"} <= kinds
