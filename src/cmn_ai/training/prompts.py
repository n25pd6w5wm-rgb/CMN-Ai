"""Shared routing prompt template + classification (de)serialization.

One source of truth used by both training data generation and live inference, so the
fine-tuned model sees exactly the same instruction/format at train and run time. The
model is asked to emit a compact JSON classification; ``parse_classification`` reads it
back robustly (tolerating surrounding prose) and returns ``None`` on anything invalid,
so the caller can fall back to the rule router.
"""

from __future__ import annotations

import json
import re

from cmn_ai.core import Bucket, Capability, Classification, Complexity

SYSTEM_INSTRUCTION = (
    "You are cmn-ai's routing classifier. Classify the user request and respond with "
    "ONLY a JSON object, no prose, of the form: "
    '{"capability": one of ["chat","code","research","multimodal"], '
    '"complexity": one of ["low","high"], "needs_web": true or false}. '
    "Use 'code' for programming tasks, 'research' when current/web information is needed, "
    "'multimodal' for images/audio/video, otherwise 'chat'. Use 'high' complexity for "
    "hard, long or multi-step tasks."
)

_JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)


def build_classify_messages(prompt: str) -> list[dict[str, str]]:
    """Chat messages for classifying one user prompt.

    The instruction is folded into the single user turn instead of a separate system
    message, so the identical format works across model families — notably Gemma, whose
    chat template rejects a ``system`` role. Used the same way at train and inference time.
    """
    return [
        {"role": "user", "content": f"{SYSTEM_INSTRUCTION}\n\nRequest:\n{prompt}"},
    ]


def classification_to_json(c: Classification) -> str:
    """Serialize a Classification to the compact JSON the model is trained to emit.

    Bucket is intentionally omitted — it is derived from the capability on parse.
    """
    return json.dumps(
        {
            "capability": c.capability.value,
            "complexity": c.complexity.value,
            "needs_web": c.needs_web,
        }
    )


def _bucket_for(capability: Capability) -> Bucket:
    return Bucket.CODING if capability is Capability.CODE else Bucket.GENERAL


def parse_classification(text: str) -> Classification | None:
    """Parse model output into a Classification, or None if it is not valid.

    Tolerates leading/trailing prose by extracting the first JSON object. Returns None
    on malformed JSON, missing fields, or unknown enum values.
    """
    match = _JSON_OBJECT.search(text)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        capability = Capability(data["capability"])
        complexity = Complexity(data["complexity"])
        needs_web = data["needs_web"]
    except (KeyError, ValueError):
        return None
    if not isinstance(needs_web, bool):
        return None
    return Classification(
        capability=capability,
        complexity=complexity,
        needs_web=needs_web,
        bucket=_bucket_for(capability),
    )
