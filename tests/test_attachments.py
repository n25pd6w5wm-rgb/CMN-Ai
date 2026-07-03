"""Tests for attachment extraction: any file type must become model-readable text."""

from __future__ import annotations

import base64
import io

from cmn_ai.web.attachments import (
    Attachment,
    is_image,
    mime_for,
    render_attachments,
)


def _att(name: str, raw: bytes) -> Attachment:
    return Attachment(name=name, data=base64.b64encode(raw).decode())


def _docx_bytes() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Projektplan Q3")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Meilenstein"
    table.rows[0].cells[1].text = "August"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Umsatz"
    ws.append(["Monat", "Betrag"])
    ws.append(["Juli", 1234])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pptx_bytes() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Quartalsbericht"
    box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    box.text_frame.text = "Alles im Plan"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_docx_paragraphs_and_tables_extracted() -> None:
    block = render_attachments([_att("plan.docx", _docx_bytes())])
    assert "plan.docx" in block
    assert "Projektplan Q3" in block
    assert "Meilenstein" in block
    assert "August" in block


def test_xlsx_rows_extracted_per_sheet() -> None:
    block = render_attachments([_att("zahlen.xlsx", _xlsx_bytes())])
    assert "Umsatz" in block  # sheet name announced
    assert "Monat" in block
    assert "1234" in block


def test_pptx_slide_text_extracted() -> None:
    block = render_attachments([_att("deck.pptx", _pptx_bytes())])
    assert "Quartalsbericht" in block
    assert "Alles im Plan" in block


def test_binary_file_yields_placeholder_not_mojibake() -> None:
    # dense binary (e.g. a compiled blob) must not fill the prompt with U+FFFD noise
    raw = bytes(range(256)) * 40
    block = render_attachments([_att("blob.bin", raw)])
    assert "Binärdatei" in block
    assert "�" not in block


def test_plain_text_still_extracted() -> None:
    block = render_attachments([_att("notiz.txt", "Hallo Ümläute".encode())])
    assert "Hallo Ümläute" in block


def test_is_image_and_mime_detection() -> None:
    assert is_image("foto.PNG") is True
    assert is_image("scan.jpeg") is True
    assert is_image("bericht.pdf") is False
    assert mime_for("foto.png") == "image/png"
    assert mime_for("scan.jpg") == "image/jpeg"


def test_images_are_skipped_in_text_block() -> None:
    # images travel to the model as image parts, not as garbled text
    block = render_attachments(
        [_att("foto.png", b"\x89PNG\r\n\x1a\nxxxx"), _att("notiz.txt", b"lesbar")]
    )
    assert "lesbar" in block
    assert "foto.png" not in block


def test_char_budget_still_caps_office_files() -> None:
    big = ("Zeile mit Inhalt\n" * 20_000).encode()
    block = render_attachments([_att("gross.txt", big)])
    assert len(block) < 60_000
    assert "gekürzt" in block
