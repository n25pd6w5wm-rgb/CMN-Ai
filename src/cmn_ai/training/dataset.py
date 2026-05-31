"""Build the LoRA training dataset from a curated seed + the live decision log.

Output is mlx-lm chat JSONL (``{"messages": [system, user, assistant]}``) where the
assistant turn is the JSON classification the model must learn to produce. The seed
covers all classes for a meaningful cold start (the live log is small and initially
just mirrors the rule router); logged decisions broaden it as real usage accrues.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cmn_ai.core import Bucket, Capability, Classification, Complexity
from cmn_ai.storage.decisions import DecisionLog
from cmn_ai.training.prompts import build_classify_messages, classification_to_json

SEED_PATH = Path(__file__).resolve().parent / "seed.jsonl"


@dataclass(frozen=True, slots=True)
class LabeledPrompt:
    """A training example: a user prompt and its target classification."""

    prompt: str
    classification: Classification


def _classification(capability: str, complexity: str, needs_web: bool) -> Classification:
    cap = Capability(capability)
    return Classification(
        capability=cap,
        complexity=Complexity(complexity),
        needs_web=needs_web,
        bucket=Bucket.CODING if cap is Capability.CODE else Bucket.GENERAL,
    )


def load_seed(path: Path | str) -> list[LabeledPrompt]:
    """Load curated labeled prompts from a JSONL seed file."""
    samples: list[LabeledPrompt] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        samples.append(
            LabeledPrompt(
                prompt=row["prompt"],
                classification=_classification(
                    row["capability"], row["complexity"], bool(row["needs_web"])
                ),
            )
        )
    return samples


def samples_from_log(log: DecisionLog, limit: int = 5000) -> list[LabeledPrompt]:
    """Turn logged routing decisions into labeled prompts."""
    samples: list[LabeledPrompt] = []
    for row in log.recent(limit=limit):
        prompt = row["prompt"]
        if not prompt:
            continue
        samples.append(
            LabeledPrompt(
                prompt=prompt,
                classification=_classification(
                    row["capability"], row["complexity"], bool(row["needs_web"])
                ),
            )
        )
    return samples


def build_samples(seed_path: Path | str, log: DecisionLog | None = None) -> list[LabeledPrompt]:
    """Combine seed + logged samples, de-duplicating by prompt (log wins)."""
    by_prompt: dict[str, LabeledPrompt] = {s.prompt: s for s in load_seed(seed_path)}
    if log is not None:
        for s in samples_from_log(log):
            by_prompt[s.prompt] = s
    return list(by_prompt.values())


def build_chat_jsonl(samples: list[LabeledPrompt]) -> list[dict[str, Any]]:
    """Format samples as mlx-lm chat records with the classification as the target."""
    rows: list[dict[str, Any]] = []
    for s in samples:
        messages = build_classify_messages(s.prompt)
        messages.append({"role": "assistant", "content": classification_to_json(s.classification)})
        rows.append({"messages": messages})
    return rows


def split(
    samples: list[LabeledPrompt],
    *,
    valid_frac: float = 0.15,
    test_frac: float = 0.15,
    seed: int = 0,
) -> tuple[list[LabeledPrompt], list[LabeledPrompt], list[LabeledPrompt]]:
    """Deterministically shuffle and split into (train, valid, test)."""
    shuffled = list(samples)
    random.Random(seed).shuffle(shuffled)
    n = len(shuffled)
    n_valid = int(n * valid_frac)
    n_test = int(n * test_frac)
    test = shuffled[:n_test]
    valid = shuffled[n_test : n_test + n_valid]
    train = shuffled[n_test + n_valid :]
    return train, valid, test


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def write_dataset(
    out_dir: Path | str,
    train: list[LabeledPrompt],
    valid: list[LabeledPrompt],
    test: list[LabeledPrompt],
) -> None:
    """Write train/valid/test JSONL files in the mlx-lm data-dir layout."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "train.jsonl", build_chat_jsonl(train))
    _write_jsonl(out / "valid.jsonl", build_chat_jsonl(valid))
    _write_jsonl(out / "test.jsonl", build_chat_jsonl(test))
