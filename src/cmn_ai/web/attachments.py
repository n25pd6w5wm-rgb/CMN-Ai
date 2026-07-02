"""Turn uploaded chat attachments (PDF, text, code, …) into model-readable context.

The client sends files base64-encoded inside the chat request; this module decodes
them, extracts text (pypdf for PDFs, UTF-8 for everything else) and renders one
capped context block. The cap protects the prompt from multi-hundred-page uploads —
the budget governor estimates costs from prompt length, so unbounded attachments
would mean unbounded spend.
"""

from __future__ import annotations

import base64
import io

from pydantic import BaseModel

_MAX_TOTAL_CHARS = 40_000
MAX_ATTACHMENTS = 8


class Attachment(BaseModel):
    name: str
    data: str  # base64-encoded file contents


def _extract_text(name: str, raw: bytes) -> str:
    if name.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return raw.decode("utf-8", errors="replace")


def render_attachments(attachments: list[Attachment]) -> str:
    """One prompt block announcing every file with its (capped) extracted text."""
    parts: list[str] = []
    budget = _MAX_TOTAL_CHARS
    for att in attachments[:MAX_ATTACHMENTS]:
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
