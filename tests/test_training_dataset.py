"""Tests for building the LoRA dataset from seed data + the decision log."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cmn_ai.core import (
    AgentResponse,
    Bucket,
    Capability,
    Classification,
    Complexity,
    RouteDecision,
    Usage,
)
from cmn_ai.storage.decisions import DecisionLog
from cmn_ai.training.dataset import (
    SEED_PATH,
    LabeledPrompt,
    build_chat_jsonl,
    load_seed,
    samples_from_log,
    split,
    write_dataset,
)
from cmn_ai.training.prompts import parse_classification

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _write_seed(path: Path) -> None:
    lines = [
        {"prompt": "hello", "capability": "chat", "complexity": "low", "needs_web": False},
        {"prompt": "fix my code", "capability": "code", "complexity": "high", "needs_web": False},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_shipped_seed_loads_and_covers_all_capabilities() -> None:
    samples = load_seed(SEED_PATH)
    assert len(samples) >= 40
    covered = {s.classification.capability for s in samples}
    assert covered == set(Capability)
    # research examples should be marked as needing the web
    research = [s for s in samples if s.classification.capability is Capability.RESEARCH]
    assert all(s.classification.needs_web for s in research)


def test_load_seed_parses_labeled_prompts(tmp_path: Path) -> None:
    seed = tmp_path / "seed.jsonl"
    _write_seed(seed)
    samples = load_seed(seed)
    assert len(samples) == 2
    code = samples[1]
    assert code.prompt == "fix my code"
    assert code.classification.capability is Capability.CODE
    assert code.classification.bucket is Bucket.CODING


def test_samples_from_log_reads_logged_decisions(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.db")
    classification = Classification(
        capability=Capability.RESEARCH,
        complexity=Complexity.LOW,
        needs_web=True,
        bucket=Bucket.GENERAL,
    )
    decision = RouteDecision(
        classification=classification,
        agent="perplexity",
        model="sonar-pro",
        reason="r",
        estimated_eur=0.01,
    )
    response = AgentResponse(text="x", agent="perplexity", model="sonar-pro", usage=Usage(1, 1))
    log.log("latest news?", decision, response, at=NOW)

    samples = samples_from_log(log)
    assert len(samples) == 1
    assert samples[0].prompt == "latest news?"
    assert samples[0].classification.capability is Capability.RESEARCH
    assert samples[0].classification.needs_web is True


def test_build_chat_jsonl_round_trips_label(tmp_path: Path) -> None:
    sample = LabeledPrompt(
        prompt="write a function",
        classification=Classification(
            capability=Capability.CODE,
            complexity=Complexity.HIGH,
            needs_web=False,
            bucket=Bucket.CODING,
        ),
    )
    rows = build_chat_jsonl([sample])
    assert len(rows) == 1
    messages = rows[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "write a function"}
    assert messages[2]["role"] == "assistant"
    # the assistant target must parse back to the same classification
    assert parse_classification(messages[2]["content"]) == sample.classification


def test_split_is_deterministic_and_disjoint() -> None:
    samples = [
        LabeledPrompt(
            prompt=f"q{i}",
            classification=Classification(
                capability=Capability.CHAT,
                complexity=Complexity.LOW,
                needs_web=False,
                bucket=Bucket.GENERAL,
            ),
        )
        for i in range(10)
    ]
    train, valid, test = split(samples, valid_frac=0.2, test_frac=0.2, seed=42)
    assert (len(train), len(valid), len(test)) == (6, 2, 2)
    again = split(samples, valid_frac=0.2, test_frac=0.2, seed=42)
    assert [s.prompt for s in train] == [s.prompt for s in again[0]]
    all_prompts = {s.prompt for s in train + valid + test}
    assert len(all_prompts) == 10  # no overlap, nothing lost


def test_write_dataset_writes_three_files(tmp_path: Path) -> None:
    def _mk(p: str) -> LabeledPrompt:
        return LabeledPrompt(
            prompt=p,
            classification=Classification(
                capability=Capability.CHAT,
                complexity=Complexity.LOW,
                needs_web=False,
                bucket=Bucket.GENERAL,
            ),
        )

    out = tmp_path / "data"
    write_dataset(out, [_mk("a"), _mk("b")], [_mk("c")], [_mk("d")])
    assert (out / "train.jsonl").read_text().count("\n") == 2
    assert (out / "valid.jsonl").read_text().count("\n") == 1
    assert (out / "test.jsonl").read_text().count("\n") == 1
    # each line is a valid chat record
    first = json.loads((out / "train.jsonl").read_text().splitlines()[0])
    assert "messages" in first
