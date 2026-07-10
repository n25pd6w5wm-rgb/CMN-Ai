"""Document skills — per-document-type guidance injected into the system prompt.

Like Claude's skills, these are compact instruction blocks loaded on demand: when a
request looks like "make me a presentation / report / budget sheet / letter / CV",
the matching block is appended to the system prompt (see ``Orchestrator``), so the
model produces a professionally structured, well-designed document. Ordinary chat
turns never pay their token cost.

Keynote and Numbers have no writable Python format — the skills steer those requests
to .pptx / .xlsx, which both apps open natively.
"""

from __future__ import annotations

# Keyword groups (matched lowercased, substring — stems cover German inflection).
# Order matters: the first matching skill wins, so the more specific document kinds
# (slides, sheets, letters, CVs) are checked before the generic report/PDF bucket.
_SKILL_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "presentation",
        ("präsentation", "presentation", "folien", "slides", "pptx", "keynote", "vortrag"),
    ),
    (
        "spreadsheet",
        (
            "excel",
            "xlsx",
            "numbers-datei",
            "numbers datei",
            "tabelle",
            "spreadsheet",
            "kalkulation",
            "budgetplan",
            "haushaltsplan",
        ),
    ),
    ("letter", ("anschreiben", "brief ", "brief.", "formeller brief", "cover letter", "kündigung")),
    ("cv", ("lebenslauf", "curriculum vitae", " cv", "resume", "résumé")),
    (
        "report",
        (
            "bericht",
            "report",
            "analyse als",
            "dokument",
            "als pdf",
            "als docx",
            "word-datei",
            "word datei",
            "handout",
            "zusammenfassung als",
        ),
    ),
)

_SKILLS: dict[str, str] = {
    "report": (
        "SKILL report — professional document (.pdf / .docx):\n"
        "- Emit ONE cmn:file fence (extension .pdf or .docx); theme: 'report' for "
        "business/analysis, 'elegant' for editorial/essays, 'modern' for proposals, "
        "'minimal' for plain technical docs, 'warm' for personal/brand pieces.\n"
        "- Structure: '# Title' → short executive summary (2-4 sentences) → '##' "
        "sections in a logical arc → '## Fazit' with concrete takeaways.\n"
        "- Every claim with numbers gets a Markdown table; every process gets an "
        "ordered list; key insights as '>' pull-quotes. Aim for 2-5 pages of "
        "substance — never filler, never an unfinished section.\n"
    ),
    "presentation": (
        "SKILL presentation — slide deck (.pptx; opens natively in Keynote too):\n"
        "- Emit ONE cmn:file fence with a .pptx name; theme 'deck' (or 'warm'/"
        "'minimal' when the topic calls for it). Slides split on '##' headings.\n"
        "- Title slide comes from '# Title'. Then 5-10 '##' slides: one idea per "
        "slide, 3-5 tight bullets each (max ~10 words per bullet), never paragraphs.\n"
        "- Comparisons and figures as Markdown tables (they become real slide "
        "tables). Finish with a '## Takeaways' or '## Nächste Schritte' slide.\n"
        "- If the user says 'Keynote': produce .pptx — Keynote opens it directly.\n"
    ),
    "spreadsheet": (
        "SKILL spreadsheet — workbook (.xlsx; opens natively in Numbers and Excel):\n"
        "- Emit ONE cmn:file fence with an .xlsx name. Each Markdown table becomes "
        "its own sheet, named after the '##' heading right above it — so give every "
        "table a heading.\n"
        "- First row = column headers. Keep numeric columns purely numeric (no "
        "units inside the cells; put units in the header, e.g. 'Betrag (€)') so "
        "sums and sorting work.\n"
        "- Prose outside tables lands on an overview sheet — use a short intro "
        "paragraph explaining the workbook, then the tables.\n"
        "- If the user says 'Numbers' or 'Excel': .xlsx is correct for both.\n"
    ),
    "letter": (
        "SKILL letter — formal letter / cover letter (.pdf or .docx):\n"
        "- Emit ONE cmn:file fence; theme 'elegant' (restrained) or 'minimal'.\n"
        "- Structure: sender block, date, recipient block, subject line in bold, "
        "salutation, 3-4 focused paragraphs, closing formula, name.\n"
        "- German business letters: 'Sehr geehrte/r …', close with 'Mit freundlichen "
        "Grüßen'. Keep to ONE page; no tables, no bullet spam — flowing prose.\n"
        "- Use placeholders like [Adresse] only for facts the user did not provide.\n"
    ),
    "cv": (
        "SKILL cv — Lebenslauf / résumé (.pdf or .docx):\n"
        "- Emit ONE cmn:file fence; theme 'minimal' or 'modern'. ONE page if "
        "possible, two at most.\n"
        "- Structure: '# Name' + one-line profile, then '## Berufserfahrung' "
        "(reverse-chronological; each role as bold title + dates, 2-3 bullet "
        "achievements with numbers), '## Ausbildung', '## Kenntnisse' (compact "
        "table or bullets), optional '## Projekte'.\n"
        "- Achievements over duties: 'X um 30 % verbessert' beats 'zuständig für X'. "
        "Never invent facts — leave [Platzhalter] where information is missing.\n"
    ),
}


def detect_doc_skill(prompt: str) -> str | None:
    """Return the skill name a prompt calls for, or None for ordinary chat."""
    lower = prompt.lower()
    for name, keywords in _SKILL_KEYWORDS:
        if any(k in lower for k in keywords):
            return name
    return None


def skill_text(name: str) -> str:
    """The instruction block for a skill name ('' if unknown)."""
    return _SKILLS.get(name, "")
