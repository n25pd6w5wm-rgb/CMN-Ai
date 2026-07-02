"""Render an assistant answer (lightweight Markdown) into real documents.

Kept deliberately simple: headings (#/##), bullets (-/*) and paragraphs — enough to
turn a chat answer into a presentable PDF or a slide deck without a headless browser.
PDF uses fpdf2 core fonts (latin-1 covers German); characters outside latin-1 are
replaced rather than crashing the export.
"""

from __future__ import annotations

import io
from dataclasses import dataclass


@dataclass
class _Block:
    kind: str  # "h1" | "h2" | "bullet" | "text"
    text: str


def _parse(content: str) -> list[_Block]:
    blocks: list[_Block] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            blocks.append(_Block("h2", line[3:].strip()))
        elif line.startswith("# "):
            blocks.append(_Block("h1", line[2:].strip()))
        elif line.startswith(("- ", "* ")):
            blocks.append(_Block("bullet", line[2:].strip()))
        else:
            blocks.append(_Block("text", line))
    return blocks


def _latin1(text: str) -> str:
    return text.encode("latin-1", errors="replace").decode("latin-1")


def to_pdf(content: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    for block in _parse(content):
        text = _latin1(block.text)
        if block.kind == "h1":
            pdf.set_font("Helvetica", "B", 18)
            pdf.multi_cell(0, 9, text)
            pdf.ln(2)
        elif block.kind == "h2":
            pdf.set_font("Helvetica", "B", 14)
            pdf.multi_cell(0, 7, text)
            pdf.ln(1)
        elif block.kind == "bullet":
            pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(0, 6, _latin1(f"• {block.text}"))
        else:
            pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(0, 6, text)
            pdf.ln(1)
    return bytes(pdf.output())


def to_pptx(content: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    blocks = _parse(content)

    # First h1 becomes the title slide; every h2 (or h1 after that) starts a slide.
    title = next((b.text for b in blocks if b.kind == "h1"), "cmn-ai")
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    if slide.placeholders and len(slide.placeholders) > 1:
        slide.placeholders[1].text = "erstellt mit cmn·ai"

    body = None
    for block in blocks:
        if block.kind in ("h1", "h2"):
            if block.text == title and body is None:
                continue
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = block.text
            body = slide.placeholders[1].text_frame
            body.clear()
            continue
        if body is None:  # content before any section heading → its own slide
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = title
            body = slide.placeholders[1].text_frame
            body.clear()
        para = body.paragraphs[0] if not body.paragraphs[0].text else body.add_paragraph()
        para.text = block.text
        para.level = 1 if block.kind == "bullet" else 0
        para.font.size = Pt(18)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
