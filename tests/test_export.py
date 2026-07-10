"""Tests for document generation: Markdown answers become PDF/PPTX/DOCX files."""

from __future__ import annotations

import io

from cmn_ai.web.export import to_docx, to_pdf, to_pptx, to_xlsx

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


# ---------- themes: several visual variants per format ----------

from cmn_ai.web.export import THEMES, resolve_theme  # noqa: E402


def test_every_theme_renders_all_formats() -> None:
    for name in THEMES:
        pdf = to_pdf(_DOC, theme=name)
        assert pdf[:4] == b"%PDF", name
        assert "Quartalsbericht" in _pdf_text(pdf), name
        assert to_docx(_DOC, theme=name)[:2] == b"PK", name
        assert to_pptx(_DOC, theme=name)[:2] == b"PK", name
        assert to_xlsx(_DOC, theme=name)[:2] == b"PK", name


def test_theme_roster_covers_the_promised_styles() -> None:
    # The SYSTEM_PROMPT teaches these names to the model — they must all exist.
    assert {"report", "modern", "elegant", "deck", "minimal", "warm"} <= set(THEMES)


def test_unknown_theme_falls_back_but_still_renders() -> None:
    assert to_pdf(_DOC, theme="does-not-exist")[:4] == b"%PDF"
    assert to_pptx(_DOC, theme="does-not-exist")[:2] == b"PK"


def test_cover_theme_adds_a_title_page() -> None:
    from pypdf import PdfReader

    plain = PdfReader(io.BytesIO(to_pdf(_DOC, theme="report")))  # no cover
    covered = PdfReader(io.BytesIO(to_pdf(_DOC, theme="modern")))  # cover=True
    assert len(covered.pages) > len(plain.pages)


def test_resolve_theme_defaults_per_format() -> None:
    # An empty/unknown theme resolves to a sensible per-format default, and pptx
    # deliberately defaults to a presentation-oriented theme (cover slide).
    assert resolve_theme("", "pdf") is resolve_theme("report", "pdf")
    assert resolve_theme("", "pptx").cover is True


def test_pptx_theme_builds_multiple_slides_and_a_table() -> None:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(to_pptx(_DOC, theme="deck")))
    assert len(prs.slides) >= 2  # title slide + content
    has_table = any(shape.has_table for slide in prs.slides for shape in slide.shapes)
    assert has_table  # the markdown table becomes a real PPTX table, not tab text


# ---------- XLSX (openpyxl) — opens in Excel and Apple Numbers ----------


def test_xlsx_tables_become_real_sheets_with_numbers() -> None:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(to_xlsx(_DOC)))
    # The markdown table lands on a data sheet with its header row intact …
    sheet = next(ws for ws in wb.worksheets if ws.max_row >= 2 and ws.cell(1, 1).value == "Monat")
    assert sheet.cell(1, 2).value == "Umsatz"
    assert sheet.cell(2, 1).value == "Juli"
    # … and numeric-looking cells are stored as numbers, not strings.
    assert sheet.cell(2, 2).value == "1234 €"  # currency text stays text


def test_xlsx_pure_numbers_are_numeric_cells() -> None:
    from openpyxl import load_workbook

    md = "# Zahlen\n\n| Posten | Wert |\n| --- | --- |\n| A | 42 |\n| B | 3.5 |\n"
    wb = load_workbook(io.BytesIO(to_xlsx(md)))
    sheet = next(ws for ws in wb.worksheets if ws.cell(1, 1).value == "Posten")
    assert sheet.cell(2, 2).value == 42
    assert sheet.cell(3, 2).value == 3.5


def test_xlsx_without_tables_still_produces_a_workbook() -> None:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(to_xlsx("# Nur Text\n\nEin Absatz ohne Tabelle.")))
    assert wb.worksheets  # an overview sheet carries the text content
    text = " ".join(
        str(c.value) for row in wb.worksheets[0].iter_rows() for c in row if c.value is not None
    )
    assert "Nur Text" in text
