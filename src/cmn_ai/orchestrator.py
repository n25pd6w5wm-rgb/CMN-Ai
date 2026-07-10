"""Orchestrator — the per-request glue tying router, agents and governor together.

Flow per request (see plan's data flow): classify (free, local) -> select an agent
under the budget -> optionally optimise the prompt -> run the agent -> book the actual
spend in the ledger -> return the routing decision plus the response. A blocked
decision short-circuits: no agent runs and nothing is billed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime

from cmn_ai.agents.base import Agent, HealthCheckedAgent
from cmn_ai.budget.governor import BudgetGovernor
from cmn_ai.core import (
    AgentResponse,
    Bucket,
    Capability,
    Classification,
    Complexity,
    RouteDecision,
    Task,
    Usage,
)
from cmn_ai.doc_skills import detect_doc_skill, skill_text
from cmn_ai.router.interface import Router
from cmn_ai.storage.decisions import DecisionLog

# Team ("council") mode: how many AIs draft an answer before one merges them.
_COUNCIL_MAX = 3
# The panel is built for strength + diversity, not cheapness: a strong reasoner
# (Claude), a web researcher (Perplexity) and a second strong generalist (GPT) —
# the cheap flash model only joins when the stronger ones are unavailable.
_COUNCIL_PREFERENCE = ("anthropic", "perplexity", "openai", "coding", "gemini", "local")
# When several succeed, the strongest available merges — preference order.
_MERGE_PREFERENCE = ("anthropic", "coding", "openai", "gemini", "perplexity", "local")
_MERGE_SYSTEM = (
    "You are the lead of a panel of AIs. Several assistants each drafted an answer to "
    "the same question. Merge them into one single, best answer in the user's language: "
    "keep what they agree on, resolve contradictions sensibly, add nothing false, and "
    "drop repetition. Write it as one coherent, well-structured Markdown reply — do not "
    "mention that drafts existed or that you merged them."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# Auto-team ("does this deserve a panel?"): a deliberately low-threshold heuristic. The
# user tunes eagerness per chat via ``mode`` — "spar" (budget-conscious, only when it
# clearly helps) or "power" (a team for almost anything non-trivial). The budget governor
# stays the hard ceiling in both; mode only changes how often a team convenes and how
# large it may get.
_COUNCIL_MIN_CHARS = 20
_COUNCIL_THRESHOLD = {"power": 1, "spar": 3}
_COUNCIL_POWER_LENGTH = 60
_COUNCIL_KEYWORDS = (
    "vergleich",
    "compare",
    "pro und contra",
    "pros and cons",
    "vor- und nachteile",
    "vor und nachteile",
    "bewerte",
    "beurteile",
    "meinung",
    "perspektive",
    "optionen",
    "welche ist besser",
    "was ist besser",
    "empfiehl",
    "empfehlung",
    "strategie",
    "entscheide",
    "entscheidung",
    "abwägen",
    "trade-off",
    "tradeoff",
)


def council_score(prompt: str, classification: Classification) -> int:
    """Score 'would several AIs help here?' — higher means more worth a team."""
    lower = prompt.lower()
    score = 0
    if classification.complexity is Complexity.HIGH:
        score += 2
    if classification.capability is Capability.RESEARCH or classification.needs_web:
        score += 1
    if len(prompt) > 240:
        score += 1
    if prompt.count("?") >= 2:
        score += 1
    # A compare / evaluate / recommend ask is the strongest "several views help" signal —
    # weighted so it alone crosses the budget-conscious ("spar") threshold.
    if any(k in lower for k in _COUNCIL_KEYWORDS):
        score += 3
    return score


def wants_council(prompt: str, classification: Classification, mode: str = "spar") -> bool:
    """Decide whether to auto-convene a team, given the user's eagerness ``mode``.

    Trivial prompts (greetings, one-liners) never convene a team. In 'power' mode any
    substantial prompt does; in 'spar' mode only ones with a real multi-perspective
    signal. Actual spend is still capped by the budget governor downstream.
    """
    stripped = prompt.strip()
    if len(stripped) < _COUNCIL_MIN_CHARS:
        return False
    if mode == "power" and len(stripped) >= _COUNCIL_POWER_LENGTH:
        return True
    threshold = _COUNCIL_THRESHOLD.get(mode, _COUNCIL_THRESHOLD["spar"])
    return council_score(prompt, classification) >= threshold


SYSTEM_PROMPT = (
    "You are cmn-ai, a capable, thorough assistant.\n"
    "\n"
    "Style: Always answer in the user's language (German users get German answers). "
    "Write well-structured Markdown — use headings, lists, tables and code blocks "
    "where they genuinely help. Give substantial questions substantial answers with "
    "reasoning and examples; keep trivial questions short. Never pad, but never "
    "compress a real answer down to a single sentence either. If you are unsure, "
    "say so rather than inventing facts.\n"
    "\n"
    "Reasoning: For complex, multi-step or quantitative questions, reason the problem "
    "through step by step before you commit to an answer, and double-check facts, "
    "figures and calculations. Show the key reasoning where it helps the reader follow "
    "your conclusion; keep it brief for simple questions.\n"
    "\n"
    "Files: When the user asks for a document, report, presentation or any file "
    "(e.g. 'als PDF', 'erstelle eine Präsentation', 'gib mir ein Word-Dokument'), "
    "write the COMPLETE document content inside a fenced block of this exact form:\n"
    '```cmn:file name="dateiname.pdf" theme="modern"\n'
    "# Titel\n"
    "…full, polished Markdown content of the document — not a summary…\n"
    "```\n"
    "Use the file extension the user wants: .pdf, .docx, .pptx (slides split on "
    "## headings) or .xlsx (each Markdown table becomes a sheet). Keynote and "
    "Numbers users get .pptx / .xlsx — both open natively there. Put your normal "
    "reply before or after the block. The system turns the block into a real "
    "downloadable file automatically.\n"
    "\n"
    "Design: pick a visual theme that fits the document and add it as "
    'theme="…" on the fence (optional; a sensible default is used if you omit it). '
    "Themes: 'report' (formal, blue — business reports, analyses), 'modern' (teal, "
    "cover page — proposals, product docs), 'elegant' (restrained terracotta, cover "
    "page — essays, letters, editorial), 'deck' (indigo, cover slide — the best fit "
    "for .pptx presentations), 'minimal' (black & white, editorial restraint — "
    "technical docs, CVs), 'warm' (bronze/cream, cover page — personal, brand, "
    "culture pieces). Choose the one whose tone matches the content.\n"
    "\n"
    "Document quality (important — these render to a real, professionally typeset file):\n"
    "- Open with a single '# Title', then organise the body under '##' / '###' "
    "sections; add a short intro and a closing summary or conclusion.\n"
    "- Design like a designer would: lead each section with one clear idea, use tables "
    "for anything comparative, and keep slides (.pptx) to a few tight bullets per "
    "'##' section rather than dense paragraphs.\n"
    "- Use Markdown tables for figures/comparisons, ordered lists for steps, "
    "bullet lists for points, '>' for key takeaways, and fenced code for code.\n"
    "- Write the document to its natural end — never stop mid-sentence or mid-section. "
    "If the topic is large, be concise per section rather than leaving it unfinished, "
    "so the whole document is complete and self-contained.\n"
    "- Keep the document inside the single fenced block; don't split it."
)


def _system_for(task: Task) -> str:
    """The system prompt for a task: base prompt + on-demand document skill.

    Skills (see ``doc_skills``) are compact per-document-type instruction blocks —
    appended only when the request looks like a document, so ordinary chat turns
    never pay their token cost. Detection runs on the user's original wording.
    """
    skill = detect_doc_skill(task.prompt)
    if skill:
        return f"{SYSTEM_PROMPT}\n\n{skill_text(skill)}"
    return SYSTEM_PROMPT


class Orchestrator:
    """Coordinates a single chat request end to end."""

    def __init__(
        self,
        *,
        agents: Mapping[str, Agent],
        router: Router,
        governor: BudgetGovernor,
        decision_log: DecisionLog | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._agents = agents
        self._router = router
        self._governor = governor
        self._decision_log = decision_log
        self._now = now

    def route(self, task: Task, *, agent_override: str | None = None) -> RouteDecision:
        """Classify and select without running anything (used by route-debug).

        If ``agent_override`` names an active agent, the user picked it explicitly — use
        it (still subject to the budget) instead of letting the router choose.
        """
        classification = self._router.classify(task)
        if agent_override and agent_override in self._agents:
            agent = self._agents[agent_override]
            if agent.active:
                cost = agent.cost_per_mtok
                is_free = cost.input_eur == 0 and cost.output_eur == 0
                est = 0.0 if is_free else cost.estimate(max(1, len(task.prompt) // 4), 800)
                if is_free or self._governor.can_spend(agent.bucket, est):
                    return RouteDecision(
                        classification=classification,
                        agent=agent.name,
                        model=agent.model,
                        reason=f"user-selected {agent.name}",
                        estimated_eur=est,
                    )
                return RouteDecision(
                    classification=classification,
                    agent=agent.name,
                    model=agent.model,
                    reason="weekly budget for this category is exhausted",
                    estimated_eur=est,
                    blocked=True,
                )
        return self._router.select(task, classification, self._agents, self._governor)

    async def handle(
        self, task: Task, *, agent_override: str | None = None
    ) -> tuple[RouteDecision, AgentResponse | None]:
        """Route the task, run the chosen agent, and book its spend.

        If the router-chosen agent fails to run (e.g. the local model's tunnel is
        down), the request fails over to the next best agent instead of erroring —
        the decision then carries ``fell_back=True`` and names the dead agent, so
        the UI stays transparent about what happened. An explicitly user-selected
        agent is never silently rerouted.
        """
        # Discover backend availability first (e.g. is the Pi reachable?) so the
        # router only ever considers agents that can actually answer.
        for candidate_agent in self._agents.values():
            if isinstance(candidate_agent, HealthCheckedAgent):
                await candidate_agent.refresh_health()

        decision = self.route(task, agent_override=agent_override)
        if decision.blocked:
            self._log(task.prompt, decision, None)
            return decision, None

        system = _system_for(task)  # detect document skills on the original wording
        task = await self._router.optimize_prompt(task)
        candidates = dict(self._agents)
        while True:
            agent = self._agents[decision.agent]
            try:
                response = await agent.run(task, system=system)
                break
            except Exception:
                candidates.pop(decision.agent, None)
                if agent_override or not candidates:
                    raise
                retry = self._router.select(
                    task, decision.classification, candidates, self._governor
                )
                if retry.blocked:
                    raise
                decision = replace(
                    retry,
                    reason=f"{agent.name} unavailable — rerouted: {retry.reason}",
                    fell_back=True,
                )
        self._governor.record(
            response.bucket,
            response.agent,
            response.model,
            response.usage,
            eur=response.cost_eur,
        )
        self._log(task.prompt, decision, response)
        return decision, response

    async def handle_council(
        self, task: Task, *, mode: str = "power"
    ) -> tuple[RouteDecision, AgentResponse | None]:
        """Team mode: several AIs answer in parallel, one merges them into one reply.

        Robust to the local model being down — it collaborates with whatever paid
        agents are reachable and affordable. Degrades gracefully: with a single
        available AI it just returns that answer (no merge); with none it blocks.
        ``mode`` tunes team size: "power" uses the full panel, "spar" a leaner one.
        """
        for candidate_agent in self._agents.values():
            if isinstance(candidate_agent, HealthCheckedAgent):
                await candidate_agent.refresh_health()

        classification = self._router.classify(task)
        pool = self._council_pool(task, classification, mode=mode)
        if not pool:
            decision = RouteDecision(
                classification=classification,
                agent="council",
                model="",
                reason="no AI available for the team — is a model reachable or a key set?",
                estimated_eur=0.0,
                blocked=True,
            )
            self._log(task.prompt, decision, None)
            return decision, None

        system = _system_for(task)  # detect document skills on the original wording
        task = await self._router.optimize_prompt(task)
        results = await asyncio.gather(
            *(a.run(task, system=system) for a in pool), return_exceptions=True
        )
        drafts: list[AgentResponse] = [r for r in results if isinstance(r, AgentResponse)]
        if not drafts:
            first = next((r for r in results if isinstance(r, BaseException)), None)
            raise first or RuntimeError("the team produced no answer")

        for d in drafts:
            self._governor.record(d.bucket, d.agent, d.model, d.usage, eur=d.cost_eur)

        # A single draft needs no merge — return it as the answer.
        if len(drafts) == 1:
            single = drafts[0]
            decision = self._council_decision(classification, [single], single.cost_eur)
            self._log(task.prompt, decision, single)
            return decision, single

        merged = await self._merge_drafts(task, drafts)
        total_cost = sum(d.cost_eur for d in drafts) + (merged.cost_eur if merged else 0.0)
        if merged is not None:
            self._governor.record(
                merged.bucket, merged.agent, merged.model, merged.usage, eur=merged.cost_eur
            )
            text = merged.text
            usage = merged.usage
        else:  # merge failed — fall back to a labelled join of the drafts
            text = await self._router.synthesize(task, drafts)
            usage = Usage()
        response = AgentResponse(
            text=text,
            agent="council",
            model="+".join(d.agent for d in drafts),
            usage=usage,
            cost_eur=total_cost,
            bucket=Bucket.GENERAL,
        )
        decision = self._council_decision(classification, drafts, total_cost)
        self._log(task.prompt, decision, response)
        return decision, response

    def _council_pool(
        self, task: Task, classification: object, *, mode: str = "power"
    ) -> list[Agent]:
        cap = getattr(classification, "capability", Capability.CHAT)
        capable = [
            a
            for a in self._agents.values()
            if a.active and (cap in a.capabilities or Capability.CHAT in a.capabilities)
        ]
        est_in = max(1, len(task.prompt) // 4)

        def affordable(a: Agent) -> bool:
            est = a.cost_per_mtok.estimate(est_in, 1500)
            return est == 0.0 or self._governor.can_spend(a.bucket, est)

        ready = {a.name: a for a in capable if affordable(a)}
        # strength + diversity first (Claude, Perplexity, GPT), cheap flash last;
        # any capable agent not named in the preference trails behind.
        ordered = [ready[n] for n in _COUNCIL_PREFERENCE if n in ready]
        ordered += [a for a in ready.values() if a not in ordered]
        # "spar" keeps the panel lean (cost-conscious); "power" uses the full team.
        max_n = 2 if mode == "spar" else _COUNCIL_MAX
        return ordered[:max_n]

    async def _merge_drafts(self, task: Task, drafts: list[AgentResponse]) -> AgentResponse | None:
        by_name = {d.agent: d for d in drafts}
        merger = next(
            (self._agents[n] for n in _MERGE_PREFERENCE if n in by_name and n in self._agents),
            None,
        )
        if merger is None:
            merger = self._agents[drafts[0].agent]
        blocks = "\n\n".join(
            f"[Entwurf {i + 1} · {d.agent}]\n{d.text}" for i, d in enumerate(drafts)
        )
        prompt = f"Ursprüngliche Frage:\n{task.prompt}\n\nDie Entwürfe:\n\n{blocks}"
        try:
            return await merger.run(Task(prompt=prompt), system=_MERGE_SYSTEM)
        except Exception as exc:
            print(f"[cmn-ai] council merge failed ({exc!r}); joining drafts instead")
            return None

    def _council_decision(
        self, classification: object, drafts: list[AgentResponse], cost: float
    ) -> RouteDecision:
        from cmn_ai.core import Classification

        assert isinstance(classification, Classification)
        names = "+".join(d.agent for d in drafts)
        reason = f"team of {len(drafts)} AIs" if len(drafts) > 1 else "team mode — one AI available"
        return RouteDecision(
            classification=classification,
            agent="council",
            model=names,
            reason=reason,
            estimated_eur=cost,
        )

    def _log(self, prompt: str, decision: RouteDecision, response: AgentResponse | None) -> None:
        if self._decision_log is not None:
            self._decision_log.log(prompt, decision, response, at=self._now())
