"""Tests for the Ollama-backed routing generate_fn (the Pi path for the trained Dirigent).

On the Raspberry Pi, MLX is unavailable, so the fine-tuned Gemma router is served by
Ollama (as a fused safetensors model imported by `ollama create`). This backend must send
the *exact* training prompt (Gemma single-user-turn format) via ``/api/generate`` with
``raw=True`` — not ``/api/chat``, whose derived template does not match training — and
return the model's raw text for ``parse_classification`` to read.
"""

from __future__ import annotations

import json

import httpx
import respx

from cmn_ai.router.ollama_backend import build_ollama_generate_fn
from cmn_ai.training.prompts import SYSTEM_INSTRUCTION


@respx.mock
def test_generate_fn_calls_ollama_generate_and_returns_text() -> None:
    route = respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(
            200,
            json={"model": "cmn-dirigent", "response": '{"capability":"code"}', "done": True},
        )
    )
    generate = build_ollama_generate_fn(model="cmn-dirigent", host="http://localhost:11434")

    out = generate("write a python function")

    assert route.called
    assert out == '{"capability":"code"}'


@respx.mock
def test_generate_fn_sends_raw_gemma_prompt() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"response": "{}", "done": True})

    respx.post("http://localhost:11434/api/generate").mock(side_effect=_capture)
    generate = build_ollama_generate_fn(
        model="cmn-dirigent", host="http://localhost:11434", max_new_tokens=32
    )

    generate("what is the capital of France?")

    assert captured["model"] == "cmn-dirigent"
    assert captured["raw"] is True
    assert captured["stream"] is False
    assert captured["options"] == {"num_predict": 32, "temperature": 0}
    prompt = captured["prompt"]
    assert isinstance(prompt, str)
    # Exact Gemma single-user-turn format with the folded routing instruction.
    assert prompt.startswith("<bos><start_of_turn>user\n")
    assert prompt.endswith("<end_of_turn>\n<start_of_turn>model\n")
    assert SYSTEM_INSTRUCTION in prompt
    assert "what is the capital of France?" in prompt


@respx.mock
def test_generate_fn_strips_host_trailing_slash() -> None:
    route = respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "{}", "done": True})
    )
    generate = build_ollama_generate_fn(model="cmn-dirigent", host="http://localhost:11434/")

    generate("hi")

    assert route.called
