"""Google Gemini adapter (generativelanguage v1beta).

Gemini's wire format differs from OpenAI's: messages are ``contents`` with ``parts``,
the assistant role is called ``model``, the system prompt goes in ``systemInstruction``,
and the API key is a query parameter rather than a bearer header.
"""

from __future__ import annotations

import httpx

from cmn_ai.core import AgentResponse, Bucket, Capability, CostPerMTok, Task, Usage

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiAgent:
    """Adapter for Gemini's ``:generateContent`` endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        cost_per_mtok: CostPerMTok,
        capabilities: frozenset[Capability],
        bucket: Bucket,
        timeout: float = 120.0,
    ) -> None:
        self.name = "gemini"
        self.api_key = api_key
        self.model = model
        self.cost_per_mtok = cost_per_mtok
        self.capabilities = capabilities
        self.bucket = bucket
        self._timeout = timeout

    @property
    def active(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _extract_text(data: dict[str, object]) -> str:
        """Join all text parts of the first candidate.

        Long answers arrive split across several ``parts`` and thinking models add
        ``thought`` parts — joining (and skipping thoughts) is required for a complete
        answer. Blocked/empty responses raise so the orchestrator can reroute instead
        of streaming nothing.
        """
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            feedback = data.get("promptFeedback")
            reason = "no candidates"
            if isinstance(feedback, dict) and feedback.get("blockReason"):
                reason = str(feedback["blockReason"])
            raise RuntimeError(f"Gemini returned no answer ({reason})")

        candidate = candidates[0]
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        text = ""
        if isinstance(parts, list):
            text = "".join(
                p["text"]
                for p in parts
                if isinstance(p, dict) and "text" in p and not p.get("thought")
            )
        if not text:
            finish = "unknown"
            if isinstance(candidate, dict) and candidate.get("finishReason"):
                finish = str(candidate["finishReason"])
            raise RuntimeError(f"Gemini returned an empty answer (finishReason: {finish})")
        return text

    async def run(self, task: Task, *, system: str | None = None) -> AgentResponse:
        contents: list[dict[str, object]] = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in task.history
        ]
        user_parts: list[dict[str, object]] = [{"text": task.prompt}]
        user_parts += [
            {"inline_data": {"mime_type": mime, "data": b64}} for mime, b64 in task.images
        ]
        contents.append({"role": "user", "parts": user_parts})

        payload: dict[str, object] = {"contents": contents}
        if system is not None:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{_BASE}/{self.model}:generateContent"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, params={"key": self.api_key}, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = self._extract_text(data)
        raw_usage = data.get("usageMetadata") or {}
        usage = Usage(
            tokens_in=int(raw_usage.get("promptTokenCount", 0)),
            tokens_out=int(raw_usage.get("candidatesTokenCount", 0)),
        )
        return AgentResponse(
            text=text,
            agent=self.name,
            model=self.model,
            usage=usage,
            cost_eur=self.cost_per_mtok.estimate(usage.tokens_in, usage.tokens_out),
            bucket=self.bucket,
        )
