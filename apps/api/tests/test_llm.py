from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.llm import (
    AnthropicClient,
    NoopLLMClient,
    OpenAIClient,
    build_investigation_prompt,
    parse_llm_response,
)
from app.llm.schemas import LLMResponse


def test_noop_llm_client_returns_fallback() -> None:
    client = NoopLLMClient()
    response, usage = client.complete("any prompt")
    assert response.root_cause == "LLM is disabled; falling back to deterministic diagnosis."
    assert response.confidence == "low"
    assert usage.provider == "none"
    assert usage.model == "none"
    assert usage.used_llm is False


def test_parse_llm_response_strips_markdown_fences() -> None:
    content = "```json\n" + json.dumps({
        "root_cause": "Expired payment methods.",
        "confidence": "high",
        "next_actions": ["Action 1"],
        "reasoning": "Cards expired.",
    }) + "\n```"
    response = parse_llm_response(content)
    assert response.root_cause == "Expired payment methods."
    assert response.confidence == "high"


def test_parse_llm_response_rejects_invalid_schema() -> None:
    with pytest.raises(Exception):
        parse_llm_response(json.dumps({"missing_root_cause": True}))


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, response_json: dict[str, Any]) -> None:
        self.response_json = response_json

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(self.response_json).encode(),
        )


def _openai_completion_response(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    }


def test_openai_client_parses_response() -> None:
    diagnosis = {
        "root_cause": "Billing retry webhook regression suppressed second charge attempts.",
        "confidence": "high",
        "next_actions": ["Repair retry workflow."],
        "reasoning": "Invoices failed with retry webhook errors.",
    }
    transport = _FakeTransport(_openai_completion_response(json.dumps(diagnosis)))
    client = OpenAIClient(api_key="sk-test", transport=transport)
    response, usage = client.complete("prompt")
    assert response == LLMResponse.model_validate(diagnosis)
    assert usage.provider == "openai"
    assert usage.model == "gpt-4o-mini"
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 40
    assert usage.used_llm is True
    assert usage.latency_ms >= 0


def _anthropic_message_response(content: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": content}],
        "usage": {"input_tokens": 100, "output_tokens": 35},
        "model": "claude-test",
    }


def test_anthropic_client_parses_response() -> None:
    diagnosis = {
        "root_cause": "Expired payment methods were not refreshed before renewal.",
        "confidence": "medium",
        "next_actions": ["Draft billing reminders."],
        "reasoning": "Multiple invoices failed with expired card reasons.",
    }
    transport = _FakeTransport(_anthropic_message_response(json.dumps(diagnosis)))
    client = AnthropicClient(api_key="sk-ant-test", transport=transport)
    response, usage = client.complete("prompt")
    assert response == LLMResponse.model_validate(diagnosis)
    assert usage.provider == "anthropic"
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 35
    assert usage.used_llm is True


def test_build_investigation_prompt_includes_evidence() -> None:
    prompt = build_investigation_prompt(
        incident={"title": "MRR drop", "summary": "Paid MRR dropped"},
        revenue_metrics={"metric_evidence": {"failed_invoice_count": 3}},
        account_details={
            "accounts": [
                {
                    "account_id": "acct_001",
                    "account_name": "Brightline",
                    "segment": "growth",
                    "subscription_status": "active",
                    "failed_invoices": [
                        {"invoice_id": "inv_1", "amount_cents": 10000, "failure_reason": "expired card"}
                    ],
                }
            ]
        },
        doc_results={"results": [{"source_id": "kb-1", "title": "Runbook", "snippet": "retry webhook"}]},
        support_tickets={"tickets": [{"ticket_id": "tkt_1", "account_id": "acct_001", "category": "billing", "priority": "high", "subject": "Payment failed", "description": "Card expired"}]},
    )
    assert "Brightline" in prompt
    assert "retry webhook" in prompt
    assert "Card expired" in prompt
    assert "failed_invoice_count" in prompt


def test_pricing_table_lookup() -> None:
    from app.llm.pricing import get_pricing

    pricing = get_pricing("gpt-4o-mini")
    assert pricing.input_price_per_1m_tokens > 0
    assert pricing.output_price_per_1m_tokens > 0


def test_estimate_cost_usd() -> None:
    from app.llm.pricing import estimate_cost_usd

    cost = estimate_cost_usd(
        prompt_tokens=1_000_000, completion_tokens=500_000, model="gpt-4o-mini"
    )
    assert cost > 0
    # 1M input @ 0.15 + 0.5M output @ 0.60 = 0.15 + 0.30 = 0.45
    assert round(cost, 2) == 0.45


def test_tokenizer_returns_positive_count() -> None:
    from app.llm.tokenizer import count_tokens

    count = count_tokens("The quick brown fox jumps over the lazy dog.", model="gpt-4o-mini")
    assert count > 0


def test_tokenizer_empty_string_returns_zero() -> None:
    from app.llm.tokenizer import count_tokens

    assert count_tokens("") == 0
