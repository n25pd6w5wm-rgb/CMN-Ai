"""Render an assistant answer (Markdown) into real documents (PDF/PPTX/DOCX).

The parser understands the Markdown the models actually emit: headings (#/##/###),
bullets, fenced code blocks, pipe tables, bold runs and plain paragraphs. PDF uses
the vendored DejaVu TTF fonts (full Unicode — umlauts, Greek, arrows all render);
DOCX via python-docx; PPTX via python-pptx.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent / "fonts"


@dataclass
class _Block:
    kind: str  # "h1" | "h2" | "h3" | "bullet" | "text" | "code" | "table"
    text: str
    rows: list[list[str]] = field(default_factory=list)  # for kind == "table"


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    body = line.strip().strip("|")
    return bool(body) and all(set(c.strip()) <= {"-", ":"} for c in body.split("|"))


def _parse(content: str) -> list[_Block]:
    blocks: list[_Block] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            i += 1
            continue
        if line.startswith("```"):
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            blocks.append(_Block("code", "\n".join(code)))
            continue
        if line.startswith("|") and "|" in line[1:]:
            rows = [_table_cells(line)]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not _is_separator_row(lines[i]):
                    rows.append(_table_cells(lines[i]))
                i += 1
            blocks.append(_Block("table", "", rows=rows))
            continue
        if line.startswith("### "):
            blocks.append(_Block("h3", line[4:].strip()))
        elif line.startswith("## "):
            blocks.append(_Block("h2", line[3:].strip()))
        elif line.startswith("# "):
            blocks.append(_Block("h1", line[2:].strip()))
        elif line.startswith(("- ", "* ")):
            blocks.append(_Block("bullet", line[2:].strip()))
        else:
            blocks.append(_Block("text", line))
        i += 1
    return blocks


def _plain(text: str) -> str:
    """Strip inline markers for renderers that cannot style runs."""
    return text.replace("**", "").replace("`", "")


# ---------- PDF (fpdf2 + vendored DejaVu fonts, full Unicode) ----------


def to_pdf(content: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_font("DejaVu", "", str(_FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(_FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.add_font("DejaVuMono", "", str(_FONT_DIR / "DejaVuSansMono.ttf"))
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    for block in _parse(content):
        if block.kind == "h1":
            pdf.set_font("DejaVu", "B", 18)
            pdf.multi_cell(0, 9, _plain(block.text), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif block.kind == "h2":
            pdf.set_font("DejaVu", "B", 14)
            pdf.multi_cell(0, 7, _plain(block.text), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif block.kind == "h3":
            pdf.set_font("DejaVu", "B", 12)
            pdf.multi_cell(0, 6, _plain(block.text), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif block.kind == "bullet":
            pdf.set_font("DejaVu", size=11)
            pdf.multi_cell(0, 6, f"• {block.text}", markdown=True, new_x="LMARGIN", new_y="NEXT")
        elif block.kind == "code":
            pdf.set_font("DejaVuMono", size=9)
            pdf.set_fill_color(243, 244, 246)
            pdf.multi_cell(0, 5, block.text or " ", fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif block.kind == "table" and block.rows:
            pdf.set_font("DejaVu", size=10)
            with pdf.table() as table:
                for row_cells in block.rows:
                    row = table.row()
                    for cell in row_cells:
                        row.cell(_plain(cell))
            pdf.ln(2)
        else:
            pdf.set_font("DejaVu", size=11)
            pdf.multi_cell(0, 6, block.text, markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
    return bytes(pdf.output())


# ---------- DOCX (python-docx) ----------


def _add_runs(paragraph: object, text: str) -> None:
    """Add text to a docx paragraph, honouring **bold** runs."""
    bold = False
    for chunk in text.split("**"):
        if chunk:
            run = paragraph.add_run(chunk)  # type: ignore[attr-defined]
            run.bold = bold
        bold = not bold


def to_docx(content: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    for block in _parse(content):
        if block.kind in ("h1", "h2", "h3"):
            level = {"h1": 1, "h2": 2, "h3": 3}[block.kind]
            doc.add_heading(_plain(block.text), level=level)
        elif block.kind == "bullet":
            para = doc.add_paragraph(style="List Bullet")
            _add_runs(para, block.text.replace("`", ""))
        elif block.kind == "code":
            para = doc.add_paragraph()
            run = para.add_run(block.text)
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif block.kind == "table" and block.rows:
            cols = max(len(r) for r in block.rows)
            table = doc.add_table(rows=len(block.rows), cols=cols)
            table.style = "Light Grid Accent 1"
            for r, row_cells in enumerate(block.rows):
                for c, cell in enumerate(row_cells):
                    table.rows[r].cells[c].text = _plain(cell)
        else:
            para = doc.add_paragraph()
            _add_runs(para, block.text.replace("`", ""))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- PPTX (python-pptx) ----------


def to_pptx(content: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    blocks = _parse(content)

    # First h1 becomes the title slide; every h2 (or h1 after that) starts a slide.
    title = next((b.text for b in blocks if b.kind == "h1"), "cmn-ai")
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = _plain(title)
    if slide.placeholders and len(slide.placeholders) > 1:
        slide.placeholders[1].text = "erstellt mit cmn·ai"

    body = None
    for block in blocks:
        if block.kind in ("h1", "h2"):
            if block.text == title and body is None:
                continue
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = _plain(block.text)
            body = slide.placeholders[1].text_frame
            body.clear()
            continue
        if body is None:  # content before any section heading → its own slide
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = _plain(title)
            body = slide.placeholders[1].text_frame
            body.clear()
        if block.kind == "table":
            lines = ["\t".join(_plain(c) for c in row) for row in block.rows]
        elif block.kind == "code":
            lines = block.text.splitlines()
        else:
            lines = [_plain(block.text)]
        for line in lines:
            para = body.paragraphs[0] if not body.paragraphs[0].text else body.add_paragraph()
            para.text = line
            para.level = 1 if block.kind == "bullet" else 0
            para.font.size = Pt(18)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
