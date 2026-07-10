"""Tests for document skills: per-document-type guidance injected into the prompt.

Like Claude's skills, these are compact instruction blocks loaded on demand: when a
request looks like "make me a presentation / report / budget sheet / letter / CV",
the matching skill text is appended to the system prompt so the model produces a
professionally structured, well-designed document — without paying the token cost
on every ordinary chat turn.
"""

from __future__ import annotations

from pathlib import Path

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings
from cmn_ai.core import Task
from cmn_ai.doc_skills import detect_doc_skill, skill_text
from cmn_ai.orchestrator import Orchestrator
from cmn_ai.router.rule_router import RuleRouter
from tests.test_orchestrator import NOW, _local


def test_presentation_requests_detected_in_german_and_english() -> None:
    assert detect_doc_skill("Erstelle mir eine Präsentation über Bienen als pptx") == "presentation"
    assert detect_doc_skill("make me some slides about bees") == "presentation"
    assert detect_doc_skill("bitte als Keynote für das Team-Meeting") == "presentation"


def test_spreadsheet_requests_detected() -> None:
    assert detect_doc_skill("Mach mir eine Excel-Tabelle mit dem Budget") == "spreadsheet"
    assert detect_doc_skill("erstelle eine Numbers-Datei für meine Ausgaben") == "spreadsheet"
    assert detect_doc_skill("gib mir das als xlsx Kalkulation") == "spreadsheet"


def test_report_letter_cv_detected() -> None:
    assert detect_doc_skill("Schreib einen Bericht über Q3 als PDF") == "report"
    assert detect_doc_skill("Formuliere ein Anschreiben für meine Bewerbung") == "letter"
    assert detect_doc_skill("Erstelle meinen Lebenslauf als docx") == "cv"


def test_plain_chat_gets_no_skill() -> None:
    assert detect_doc_skill("Was ist die Hauptstadt von Frankreich?") is None
    assert detect_doc_skill("hallo") is None


def test_skill_text_exists_for_every_detected_skill() -> None:
    for name in ("report", "presentation", "spreadsheet", "letter", "cv"):
        text = skill_text(name)
        assert text and "cmn:file" in text  # every skill reminds the model of the fence


async def test_orchestrator_appends_skill_to_system_prompt(tmp_path: Path) -> None:
    captured: dict[str, str | None] = {}

    local = _local()
    original_run = local.run

    async def spy_run(task: Task, *, system: str | None = None):  # type: ignore[no-untyped-def]
        captured["system"] = system
        return await original_run(task, system=system)

    local.run = spy_run  # type: ignore[method-assign]
    gov = BudgetGovernor(
        settings=BudgetSettings(monthly_budget_eur=30.0),
        ledger=Ledger(tmp_path / "l.db"),
        now=lambda: NOW,
    )
    orch = Orchestrator(agents={"local": local}, router=RuleRouter(), governor=gov)

    await orch.handle(Task(prompt="Erstelle mir eine Präsentation über Bienen"))
    assert captured["system"] is not None
    assert "slide" in captured["system"].lower()  # presentation skill was appended

    await orch.handle(Task(prompt="hallo, wie geht es dir?"))
    assert "slide deck" not in (captured["system"] or "").lower()  # plain chat: no skill
