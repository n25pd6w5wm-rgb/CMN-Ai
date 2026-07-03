"""Turn uploaded chat attachments (PDF, Office, text, code, …) into model context.

The client sends files base64-encoded inside the chat request; this module decodes
them and extracts text per format: pypdf for PDFs, python-docx / openpyxl /
python-pptx for Office files, UTF-8 for everything text-like. Images are excluded
here — they travel to a vision-capable model as image parts, not as text. One capped
context block protects the prompt from multi-hundred-page uploads — the budget
governor estimates costs from prompt length, so unbounded attachments would mean
unbounded spend.
"""

from __future__ import annotations

import base64
import io
import mimetypes

from pydantic import BaseModel, field_validator

_MAX_TOTAL_CHARS = 40_000
MAX_ATTACHMENTS = 8
_MAX_FILE_BYTES = 15 * 1024 * 1024  # mirrors the client-side cap
_XLSX_MAX_ROWS_PER_SHEET = 200
# Ratio of replacement chars above which a decode is considered binary garbage.
_BINARY_REPLACEMENT_RATIO = 0.15

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class Attachment(BaseModel):
    name: str
    data: str  # base64-encoded file contents

    @field_validator("data")
    @classmethod
    def _size_cap(cls, v: str) -> str:
        # base64 inflates by ~4/3; reject before decoding hundreds of MB
        if len(v) > _MAX_FILE_BYTES * 4 // 3 + 4:
            raise ValueError("Datei zu groß (max. 15 MB)")
        return v


def _ext(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def is_image(name: str) -> bool:
    return _ext(name) in IMAGE_EXTENSIONS


def mime_for(name: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _extract_pdf(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(raw: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(raw))
    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _extract_xlsx(raw: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"## Sheet: {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= _XLSX_MAX_ROWS_PER_SHEET:
                lines.append("[… weitere Zeilen gekürzt]")
                break
            values = ["" if v is None else str(v) for v in row]
            if any(values):
                lines.append("\t".join(values))
    return "\n".join(lines)


def _extract_pptx(raw: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(raw))
    lines: list[str] = []
    for n, slide in enumerate(prs.slides, start=1):
        lines.append(f"## Folie {n}")
        for shape in slide.shapes:
            frame = getattr(shape, "text_frame", None)
            if frame is not None and frame.text.strip():
                lines.append(frame.text.strip())
    return "\n".join(lines)


def _extract_text(name: str, raw: bytes) -> str:
    ext = _ext(name)
    if ext == ".pdf":
        return _extract_pdf(raw)
    if ext == ".docx":
        return _extract_docx(raw)
    if ext == ".xlsx":
        return _extract_xlsx(raw)
    if ext == ".pptx":
        return _extract_pptx(raw)
    decoded = raw.decode("utf-8", errors="replace")
    if decoded and decoded.count("�") / len(decoded) > _BINARY_REPLACEMENT_RATIO:
        return "[Binärdatei — Inhalt konnte nicht als Text gelesen werden]"
    return decoded.replace("�", "")


def render_attachments(attachments: list[Attachment]) -> str:
    """One prompt block announcing every non-image file with its (capped) text."""
    parts: list[str] = []
    budget = _MAX_TOTAL_CHARS
    for att in attachments[:MAX_ATTACHMENTS]:
        if is_image(att.name):
            continue
        try:
            text = _extract_text(att.name, base64.b64decode(att.data)).strip()
        except Exception:
            text = "[Inhalt konnte nicht gelesen werden]"
        snippet = text[: max(0, budget)]
        if len(snippet) < len(text):
            snippet += "\n[… gekürzt]"
        budget -= len(snippet)
        parts.append(f"[Datei: {att.name}]\n{snippet}")
        if budget <= 0:
            break
    return "\n\n".join(parts)
