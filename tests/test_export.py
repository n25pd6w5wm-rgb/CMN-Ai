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
