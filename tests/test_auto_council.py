"""Tests for the automatic team decision: does a prompt deserve a panel of AIs?

The conductor decides whether to convene a team on its own, with a deliberately low
threshold the user tunes per chat via ``mode`` ("spar" = budget-conscious, only when it
clearly helps; "power" = a team for almost anything non-trivial). The budget governor
stays the hard ceiling regardless — mode only changes eagerness and team size.
"""

from __future__ import annotations

from pathlib import Path

from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.budget.ledger import Ledger
from cmn_ai.config import BudgetSettings
from cmn_ai.core import Bucket, Capability, Classification, Complexity, Task
from cmn_ai.orchestrator import Orchestrator, council_score, wants_council
from cmn_ai.router.rule_router import RuleRouter
from tests.test_orchestrator import NOW, _paid


def _cls(
    *,
    capability: Capability = Capability.CHAT,
    complexity: Complexity = Complexity.LOW,
    needs_web: bool = False,
) -> Classification:
    return Classification(
        capability=capability,
        complexity=complexity,
        needs_web=needs_web,
        bucket=Bucket.GENERAL,
    )


def test_trivial_prompt_never_convenes_a_team() -> None:
    low = _cls()
    assert wants_council("Hallo", low, "power") is False
    assert wants_council("danke!", low, "power") is False
    assert wants_council("Was ist 2+2?", low, "spar") is False


def test_power_mode_convenes_for_any_substantial_prompt() -> None:
    # A plain, low-complexity but real question: spar stays solo, power convenes.
    prompt = "Kannst du mir erklären, wie eine Hypothek in Deutschland grundsätzlich funktioniert?"
    low = _cls()
    assert wants_council(prompt, low, "power") is True
    assert wants_council(prompt, low, "spar") is False


def test_spar_mode_convenes_on_a_real_multi_perspective_signal() -> None:
    prompt = (
        "Vergleiche die Vor- und Nachteile von Postgres und MongoDB und empfiehl eine "
        "für ein Analytics-Startup."
    )
    assert council_score(prompt, _cls()) >= 3
    assert wants_council(prompt, _cls(), "spar") is True


def test_high_complexity_research_scores_up() -> None:
    prompt = "Analysiere die aktuelle Marktlage für E-Autos in Europa mit belastbaren Quellen."
    cls = _cls(capability=Capability.RESEARCH, complexity=Complexity.HIGH, needs_web=True)
    assert council_score(prompt, cls) >= 3
    assert wants_council(prompt, cls, "spar") is True


def test_unknown_mode_falls_back_to_spar_threshold() -> None:
    prompt = "Erkläre mir bitte kurz und knapp, was Photosynthese ist und warum sie wichtig ist."
    assert wants_council(prompt, _cls(), "bogus") == wants_council(prompt, _cls(), "spar")


# ---------- mode caps the team size (spar is leaner than power) ----------


def _governor(tmp_path: Path) -> BudgetGovernor:
    return BudgetGovernor(
        settings=BudgetSettings(monthly_budget_eur=1000.0),
        ledger=Ledger(tmp_path / "l.db"),
        now=lambda: NOW,
    )


async def test_spar_mode_caps_the_panel_smaller_than_power(tmp_path: Path) -> None:
    agents = {n: _paid(n, n) for n in ("anthropic", "openai", "perplexity")}
    orch = Orchestrator(agents=agents, router=RuleRouter(), governor=_governor(tmp_path))

    _d_power, r_power = await orch.handle_council(Task(prompt="frage"), mode="power")
    _d_spar, r_spar = await orch.handle_council(Task(prompt="frage"), mode="spar")

    assert r_power is not None and r_spar is not None
    assert r_power.model.count("+") == 2  # 3 AIs in power mode
    assert r_spar.model.count("+") == 1  # only 2 AIs in spar mode
