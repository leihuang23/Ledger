from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.llm.prompts import compose_system_prompt
from app.llm.schemas import LLMResponse, LLMUsage


class VersionConfigLike(Protocol):
    model: str | None
    temperature: float | None
    max_tokens: int | None
    system_prompt: str | None


class LLMClient(Protocol):
    provider: str
    model: str
    # ``agentic`` advertises whether the client can drive the bounded ReAct
    # investigation loop (``app.agent.loop``): it requires ``complete_raw``,
    # which returns unparsed LLM text so the loop can apply its own decision
    # protocol instead of the single-shot diagnosis schema.
    agentic: bool

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        ...

    def complete_raw(self, prompt: str) -> tuple[str, LLMUsage]:
        ...


class NoopLLMClient:
    """Fallback client used when no LLM provider is configured.

    Returns a low-confidence diagnosis so the deterministic classifier remains
    the source of truth in that mode. ``agentic`` is False, so the workflow
    stays on the deterministic pipeline and never enters the tool loop.
    """

    provider: str = "none"
    model: str = "none"
    agentic: bool = False

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        raw, usage = self.complete_raw(prompt)
        return parse_llm_response(raw), usage

    def complete_raw(self, prompt: str) -> tuple[str, LLMUsage]:
        response = LLMResponse(
            root_cause="LLM is disabled; falling back to deterministic diagnosis.",
            confidence="low",
            next_actions=[],
            reasoning="No LLM provider configured.",
        )
        usage = LLMUsage(
            provider=self.provider,
            model=self.model,
            # No request was sent and no completion was generated, so token
            # usage is honestly zero; the run metadata marks llm_used=False.
            prompt_tokens=0,
            completion_tokens=0,
            used_llm=False,
            fallback_reason="llm_provider=none",
        )
        return response.model_dump_json(), usage


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
        system_prompt: str | None = None,
    ) -> None:
        self.provider = "openai"
        self.model = model
        self.agentic = True
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._system_prompt = compose_system_prompt(system_prompt)
        self._http = _HTTPClient(transport=transport)

    def complete_raw(self, prompt: str) -> tuple[str, LLMUsage]:
        started_at = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
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
        metadata = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            used_llm=True,
        )
        return content, metadata

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        content, metadata = self.complete_raw(prompt)
        return parse_llm_response(content), metadata


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
        system_prompt: str | None = None,
    ) -> None:
        self.provider = "anthropic"
        self.model = model
        self.agentic = True
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._system_prompt = compose_system_prompt(system_prompt)
        self._http = _HTTPClient(transport=transport)

    def complete_raw(self, prompt: str) -> tuple[str, LLMUsage]:
        started_at = time.perf_counter()
        payload = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "system": self._system_prompt,
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
        metadata = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            used_llm=True,
        )
        return content, metadata

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        content, metadata = self.complete_raw(prompt)
        return parse_llm_response(content), metadata


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


def build_llm_client_for_version(version_config: VersionConfigLike) -> LLMClient:
    settings = get_settings()
    model = version_config.model or settings.llm_model
    temperature = (
        version_config.temperature
        if version_config.temperature is not None
        else settings.llm_temperature
    )
    max_tokens = (
        version_config.max_tokens
        if version_config.max_tokens is not None
        else settings.llm_max_tokens
    )
    system_prompt = version_config.system_prompt or None

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return NoopLLMClient()
        return OpenAIClient(
            api_key=settings.openai_api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            system_prompt=system_prompt,
        )
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            return NoopLLMClient()
        return AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            system_prompt=system_prompt,
        )
    return NoopLLMClient()
