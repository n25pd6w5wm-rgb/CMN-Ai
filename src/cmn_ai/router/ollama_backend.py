"""Inference shim: build a routing ``generate_fn`` backed by Ollama.

This is the Raspberry Pi path for the trained Dirigent. MLX runs only on Apple Silicon,
so on the Pi the fine-tuned Gemma router is served by Ollama (see
``scripts/export-router-gguf.sh`` and ``docs/router-on-pi.md``). The returned callable
mirrors ``training.mlx_backend.build_generate_fn``: it takes a user prompt, applies the
*exact* routing chat template the model was trained on, and returns the model's raw text —
which ``MLXRouter`` then parses into a classification (falling back to rules on failure).

Why ``/api/generate`` with ``raw=True`` instead of ``/api/chat``: Ollama's chat endpoint
applies its own derived chat template, which does not match the Gemma format used at
training time and yields garbage. Sending the pre-formatted prompt raw guarantees the
model sees byte-for-byte what it was fine-tuned on.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from cmn_ai.training.prompts import build_classify_messages


def _format_gemma_prompt(prompt: str) -> str:
    """Wrap the routing instruction + request in Gemma's single-user-turn chat format.

    Matches ``tokenizer.apply_chat_template(build_classify_messages(prompt),
    add_generation_prompt=True)`` for Gemma, so the trained model sees its training format.
    """
    content = build_classify_messages(prompt)[0]["content"]
    return f"<bos><start_of_turn>user\n{content}<end_of_turn>\n<start_of_turn>model\n"


def build_ollama_generate_fn(
    *,
    model: str,
    host: str = "http://localhost:11434",
    max_new_tokens: int = 64,
    timeout: float = 30.0,
) -> Callable[[str], str]:
    """Return a prompt->text generator that classifies via an Ollama chat model."""
    endpoint = f"{host.rstrip('/')}/api/generate"

    def _generate(prompt: str) -> str:
        resp = httpx.post(
            endpoint,
            json={
                "model": model,
                "prompt": _format_gemma_prompt(prompt),
                "raw": True,
                "stream": False,
                "options": {"num_predict": max_new_tokens, "temperature": 0},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", ""))

    return _generate
