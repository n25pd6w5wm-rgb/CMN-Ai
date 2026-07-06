"""Render an assistant answer (Markdown) into polished documents (PDF/DOCX/PPTX).

The parser understands the Markdown the models emit — headings, ordered and
unordered (nestable) lists, fenced code, pipe tables, blockquotes, horizontal
rules, and inline **bold** / *italic* / ``code`` / [links] — and each renderer
turns it into a properly typeset document: accent-coloured heading hierarchy,
shaded table headers, code blocks, blockquotes and page numbers in the PDF;
native Word styles (Title, Headings, numbered/bulleted lists, Quote, shaded
code, grid tables) in the DOCX.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent / "fonts"

# Document palette (documents are always on white, independent of the app theme).
_ACCENT = (43, 127, 196)
_INK = (28, 28, 30)
_MUTED = (110, 108, 104)
_CODE_BG = (244, 245, 247)
_HEAD_BG = (237, 242, 248)
_RULE = (222, 222, 220)


# ---------- inline parsing (bold / italic / code / links) ----------

_TOKEN = re.compile(
    r"(\*\*.+?\*\*|__.+?__|\*[^*]+?\*|_[^_]+?_|`[^`]+`|\[[^\]]+\]\([^)]+\))",
    re.DOTALL,
)


def _runs(text: str) -> list[tuple[str, frozenset[str]]]:
    """Split text into styled runs: (text, {"b"?, "i"?, "code"?})."""
    out: list[tuple[str, frozenset[str]]] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], frozenset()))
        tok = m.group(0)
        if tok.startswith(("**", "__")):
            out.append((tok[2:-2], frozenset({"b"})))
        elif tok.startswith("`"):
            out.append((tok[1:-1], frozenset({"code"})))
        elif tok.startswith("["):
            lm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok)
            label, url = (lm.group(1), lm.group(2)) if lm else (tok, "")
            out.append((f"{label} ({url})" if url else label, frozenset()))
        else:
            out.append((tok[1:-1], frozenset({"i"})))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], frozenset()))
    return out or [(text, frozenset())]


def _plain(text: str) -> str:
    return "".join(t for t, _ in _runs(text))


# ---------- block parsing ----------


@dataclass
class _Block:
    kind: str  # h1|h2|h3|h4 | para | bullet | number | code | table | quote | hr
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)
    level: int = 0
    marker: str = ""


_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_HEAD = re.compile(r"^(#{1,4})\s+(.*)$")


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep_row(line: str) -> bool:
    body = line.strip().strip("|")
    return bool(body) and all(set(c.strip()) <= {"-", ":"} for c in body.split("|"))


def _parse(content: str) -> list[_Block]:
    blocks: list[_Block] = []
    lines = content.splitlines()
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(_Block("para", " ".join(para).strip()))
            para.clear()

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        # fenced code
        if stripped.startswith("```"):
            flush_para()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(_Block("code", "\n".join(code)))
            continue

        # pipe table
        if stripped.startswith("|") and "|" in stripped[1:]:
            flush_para()
            rows = [_table_cells(line)]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not _is_sep_row(lines[i]):
                    rows.append(_table_cells(lines[i]))
                i += 1
            blocks.append(_Block("table", rows=rows))
            continue

        # horizontal rule
        if stripped in ("---", "***", "___"):
            flush_para()
            blocks.append(_Block("hr"))
            i += 1
            continue

        h = _HEAD.match(line)
        if h:
            flush_para()
            blocks.append(_Block(f"h{len(h.group(1))}", h.group(2).strip()))
            i += 1
            continue

        q = _QUOTE.match(line)
        if q:
            flush_para()
            quote = [q.group(1)]
            i += 1
            while i < len(lines) and (qm := _QUOTE.match(lines[i])):
                quote.append(qm.group(1))
                i += 1
            blocks.append(_Block("quote", " ".join(quote).strip()))
            continue

        n = _NUMBER.match(line)
        if n:
            flush_para()
            blocks.append(
                _Block("number", n.group(3).strip(), level=len(n.group(1)) // 2, marker=n.group(2))
            )
            i += 1
            continue

        b = _BULLET.match(line)
        if b:
            flush_para()
            blocks.append(_Block("bullet", b.group(2).strip(), level=len(b.group(1)) // 2))
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return blocks


# ---------- PDF (fpdf2, vendored DejaVu, page numbers) ----------


def to_pdf(content: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import TableBordersLayout
    from fpdf.fonts import FontFace

    class _PDF(FPDF):
        def footer(self) -> None:
            self.set_y(-14)
            self.set_font("DejaVu", "", 8)
            self.set_text_color(*_MUTED)
            self.cell(0, 8, f"Seite {self.page_no()}", align="C")

    pdf = _PDF()
    for style, fname in (
        ("", "DejaVuSans.ttf"),
        ("B", "DejaVuSans-Bold.ttf"),
        ("I", "DejaVuSans-Oblique.ttf"),
        ("BI", "DejaVuSans-BoldOblique.ttf"),
    ):
        pdf.add_font("DejaVu", style, str(_FONT_DIR / fname))
    pdf.add_font("DejaVuMono", "", str(_FONT_DIR / "DejaVuSansMono.ttf"))
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    def runs(text: str, size: float, base: str = "", color: tuple[int, int, int] = _INK) -> None:
        lh = size * 0.52
        for txt, st in _runs(text):
            if "code" in st:
                pdf.set_font("DejaVuMono", "", size - 1)
                pdf.set_text_color(*_ACCENT)
            else:
                style = base + ("B" if "b" in st else "") + ("I" if "i" in st else "")
                pdf.set_font("DejaVu", "".join(sorted(style)), size)
                pdf.set_text_color(*color)
            pdf.write(lh, txt)
        pdf.ln(lh)

    title_done = False
    for blk in _parse(content):
        if blk.kind == "h1" and not title_done:
            title_done = True
            pdf.set_font("DejaVu", "B", 24)
            pdf.set_text_color(*_INK)
            pdf.multi_cell(0, 11, _plain(blk.text), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*_ACCENT)
            pdf.set_line_width(0.6)
            y = pdf.get_y() + 1
            pdf.line(pdf.l_margin, y, pdf.l_margin + 40, y)
            pdf.ln(6)
        elif blk.kind in ("h1", "h2", "h3", "h4"):
            size = {"h1": 17.0, "h2": 14.0, "h3": 12.0, "h4": 11.0}[blk.kind]
            pdf.ln(2)
            pdf.set_font("DejaVu", "B", size)
            pdf.set_text_color(*(_ACCENT if blk.kind in ("h1", "h2") else _INK))
            pdf.multi_cell(0, size * 0.55, _plain(blk.text), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.5)
        elif blk.kind == "para":
            runs(blk.text, 11)
            pdf.ln(1.5)
        elif blk.kind in ("bullet", "number"):
            marker = f"{blk.marker}." if blk.kind == "number" else "•"
            indent = blk.level * 7
            left = pdf.l_margin + indent
            pdf.set_left_margin(left + 7)
            pdf.set_x(left)
            pdf.set_font("DejaVu", "", 11)
            pdf.set_text_color(*_ACCENT if blk.kind == "number" else _MUTED)
            pdf.write(11 * 0.52, f"{marker} ")
            pdf.set_x(left + 7)
            runs(blk.text, 11)
            pdf.set_left_margin(20)
        elif blk.kind == "quote":
            pdf.ln(1)
            top = pdf.get_y()
            pdf.set_left_margin(26)
            pdf.set_x(26)
            runs(blk.text, 11, base="I", color=_MUTED)
            pdf.set_draw_color(*_ACCENT)
            pdf.set_line_width(1.2)
            pdf.line(21, top, 21, pdf.get_y() - 2)
            pdf.set_left_margin(20)
            pdf.ln(2)
        elif blk.kind == "code":
            pdf.ln(1)
            pdf.set_font("DejaVuMono", "", 9)
            pdf.set_text_color(*_INK)
            pdf.set_fill_color(*_CODE_BG)
            pdf.multi_cell(
                0, 5, blk.text or " ", fill=True, new_x="LMARGIN", new_y="NEXT", padding=3
            )
            pdf.ln(2)
        elif blk.kind == "table" and blk.rows:
            pdf.ln(1)
            pdf.set_font("DejaVu", "", 10)
            pdf.set_text_color(*_INK)
            head = FontFace(emphasis="BOLD", fill_color=_HEAD_BG, color=_INK)
            with pdf.table(
                headings_style=head,
                borders_layout=TableBordersLayout.HORIZONTAL_LINES,
                line_height=6,
                cell_fill_color=(252, 252, 251),
                padding=2,
            ) as table:
                for row_cells in blk.rows:
                    row = table.row()
                    for cell in row_cells:
                        row.cell(_plain(cell))
            pdf.ln(3)
        elif blk.kind == "hr":
            pdf.ln(2)
            pdf.set_draw_color(*_RULE)
            pdf.set_line_width(0.3)
            y = pdf.get_y()
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3)

    return bytes(pdf.output())


# ---------- DOCX (python-docx, native styles) ----------


def _docx_shade(paragraph: object, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()  # type: ignore[attr-defined]
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    p_pr.append(shd)


def _docx_runs(paragraph: object, text: str, *, mono: bool = False) -> None:
    for chunk, st in _runs(text):
        if not chunk:
            continue
        run = paragraph.add_run(chunk)  # type: ignore[attr-defined]
        run.bold = "b" in st
        run.italic = "i" in st
        if mono or "code" in st:
            run.font.name = "Consolas"


def to_docx(content: str) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    title_done = False
    for blk in _parse(content):
        if blk.kind == "h1" and not title_done:
            title_done = True
            doc.add_heading(_plain(blk.text), level=0)
        elif blk.kind in ("h1", "h2", "h3", "h4"):
            level = {"h1": 1, "h2": 1, "h3": 2, "h4": 3}[blk.kind]
            h = doc.add_heading(_plain(blk.text), level=level)
            for run in h.runs:
                run.font.color.rgb = RGBColor(*_ACCENT)
        elif blk.kind == "para":
            _docx_runs(doc.add_paragraph(), blk.text)
        elif blk.kind == "bullet":
            style = "List Bullet" + (f" {blk.level + 1}" if blk.level else "")
            _safe_list(doc, blk.text, style, "List Bullet")
        elif blk.kind == "number":
            style = "List Number" + (f" {blk.level + 1}" if blk.level else "")
            _safe_list(doc, blk.text, style, "List Number")
        elif blk.kind == "quote":
            para = doc.add_paragraph()
            try:
                para.style = doc.styles["Quote"]
            except KeyError:
                para.paragraph_format.left_indent = Pt(18)
            _docx_runs(para, blk.text)
        elif blk.kind == "code":
            para = doc.add_paragraph()
            _docx_shade(para, "F4F5F7")
            run = para.add_run(blk.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9.5)
        elif blk.kind == "table" and blk.rows:
            cols = max(len(r) for r in blk.rows)
            table = doc.add_table(rows=len(blk.rows), cols=cols)
            try:
                table.style = "Light Grid Accent 1"
            except KeyError:
                table.style = "Table Grid"
            for r, row_cells in enumerate(blk.rows):
                for c, cell in enumerate(row_cells):
                    para = table.rows[r].cells[c].paragraphs[0]
                    _docx_runs(para, cell)
                    if r == 0:
                        for run in para.runs:
                            run.bold = True
        elif blk.kind == "hr":
            sep = doc.add_paragraph()
            sep.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sep.add_run("• • •").font.color.rgb = RGBColor(*_MUTED)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _safe_list(doc: object, text: str, style: str, fallback: str) -> None:
    try:
        para = doc.add_paragraph(style=style)  # type: ignore[attr-defined]
    except KeyError:
        para = doc.add_paragraph(style=fallback)  # type: ignore[attr-defined]
    _docx_runs(para, text)


# ---------- PPTX (python-pptx) ----------


def to_pptx(content: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    blocks = _parse(content)
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
        if block.kind == "hr":
            continue
        if body is None:
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
            para.level = 1 if block.kind in ("bullet", "number") else 0
            para.font.size = Pt(18)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
