from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from app.llm.prompts import INVESTIGATION_SYSTEM_PROMPT
from app.llm.schemas import LLMResponse, LLMUsage
from app.llm.tokenizer import count_tokens


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        ...


class NoopLLMClient:
    """Fallback client used when no LLM provider is configured.

    Returns a low-confidence diagnosis so the deterministic classifier remains
    the source of truth in that mode.
    """

    provider: str = "none"
    model: str = "none"

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        response = LLMResponse(
            root_cause="LLM is disabled; falling back to deterministic diagnosis.",
            confidence="low",
            next_actions=[],
            reasoning="No LLM provider configured.",
        )
        usage = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=count_tokens(prompt),
            completion_tokens=count_tokens(response.root_cause),
            used_llm=False,
            fallback_reason="llm_provider=none",
        )
        return response, usage


class _HTTPClient:
    """Thin synchronous HTTP wrapper so tests can inject a transport."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        with httpx.Client(transport=self._transport) as client:
            response = client.post(url, headers=headers, json=json_payload, timeout=timeout)
            response.raise_for_status()
            return response.json()


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = "openai"
        self.model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._http = _HTTPClient(transport=transport)

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        started_at = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": INVESTIGATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        raw = self._http.post(
            url="https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json_payload=payload,
            timeout=self._timeout,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        content = raw["choices"][0]["message"]["content"]
        usage = raw.get("usage", {})
        llm_response = parse_llm_response(content)
        metadata = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            used_llm=True,
        )
        return llm_response, metadata


class AnthropicClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-3-5-haiku-latest",
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = "anthropic"
        self.model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._http = _HTTPClient(transport=transport)

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        started_at = time.perf_counter()
        payload = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "system": INVESTIGATION_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        raw = self._http.post(
            url="https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json_payload=payload,
            timeout=self._timeout,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        content = raw["content"][0]["text"]
        usage = raw.get("usage", {})
        llm_response = parse_llm_response(content)
        metadata = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            used_llm=True,
        )
        return llm_response, metadata


def parse_llm_response(content: str) -> LLMResponse:
    """Extract and validate JSON from an LLM response string."""
    text = content.strip()
    if text.startswith("```"):
        # Strip markdown code fences if present.
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    return LLMResponse.model_validate(parsed)
