"""Inference shim: build a ``generate_fn`` backed by a fine-tuned MLX model.

Apple Silicon only; ``mlx_lm`` is imported lazily so the rest of the app (and the Pi)
never needs it. The returned callable takes a user prompt, applies the shared routing
chat template, and returns the model's raw text — which the MLXRouter parses into a
classification (falling back to rules on failure).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cmn_ai.training.prompts import build_classify_messages


def build_generate_fn(
    *,
    base_model: str,
    adapter_path: Path | str,
    max_new_tokens: int = 48,
) -> Callable[[str], str]:
    """Load the base model + LoRA adapter and return a prompt->text generator."""
    from mlx_lm import generate, load  # lazy: requires Apple Silicon + mlx-lm

    model, tokenizer = load(base_model, adapter_path=str(adapter_path))

    def _generate(prompt: str) -> str:
        messages = build_classify_messages(prompt)
        formatted = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        return str(
            generate(
                model,
                tokenizer,
                prompt=formatted,
                max_tokens=max_new_tokens,
                verbose=False,
            )
        )

    return _generate
