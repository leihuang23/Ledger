from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class LLMResponse(BaseModel):
    root_cause: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"] = "low"
    next_actions: list[str] = Field(default_factory=list)
    reasoning: str = ""


class AgentToolCallDecision(BaseModel):
    """One step of the agentic investigation loop: the LLM asks to call a tool.

    Arguments are untrusted LLM output; the loop sanitizes them against known
    incident/evidence identifiers before dispatch (see ``app.agent.loop``).
    """

    decision: Literal["tool_call"] = "tool_call"
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


class AgentFinalDecision(BaseModel):
    """The LLM declares the investigation complete and proposes a diagnosis.

    This is a *proposal*, not a verdict: the deterministic evidence validator
    (``app.agent.workflow._diagnosis_is_supported_by_evidence``) gates adoption
    and falls back to the classifier when the proposal is unsupported.
    """

    decision: Literal["final"] = "final"
    root_cause: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"] = "low"
    next_actions: list[str] = Field(default_factory=list)
    reasoning: str = ""


AgentDecision = AgentToolCallDecision | AgentFinalDecision


def parse_agent_decision(content: str) -> AgentDecision:
    """Parse and validate an agentic-loop decision from raw LLM output.

    Malformed output raises ``ValueError``: the loop treats that as a
    degradation signal and falls back to deterministic evidence gathering
    rather than silently coercing the response.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent decision is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Agent decision must be a JSON object")
    decision = parsed.get("decision")
    if decision == "tool_call":
        return AgentToolCallDecision.model_validate(parsed)
    if decision == "final":
        return AgentFinalDecision.model_validate(parsed)
    raise ValueError(f"Unknown agent decision kind: {decision!r}")


class LLMUsage(BaseModel):
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    used_llm: bool = False
    fallback_reason: str | None = None
