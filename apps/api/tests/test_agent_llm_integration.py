from __future__ import annotations

from collections.abc import Generator
from typing import Callable

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.workflow import run_investigation_workflow
from app.db.base import Base
from app.llm.schemas import LLMResponse, LLMUsage
from app.models import AgentRun, Incident
from app.seed import reseed_database


class FakeLLMClient:
    provider: str = "fake"
    model: str = "fake-model"

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.call_count = 0
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        self.call_count += 1
        self.last_prompt = prompt
        usage = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1,
            used_llm=True,
        )
        return self.response, usage


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent_llm_integration_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_workflow_uses_llm_diagnosis_when_configured(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident = session.scalar(select(Incident))
        assert incident is not None
        run = AgentRun(
            id="run_llm_test",
            incident_id=incident.id,
            status="running",
            trace_id=None,
            trace_metadata={},
            input_payload={},
            token_estimate=0,
            cost_estimate_usd=0.0,
            created_at=incident.created_at,
            updated_at=incident.created_at,
        )
        session.add(run)
        session.commit()

        llm_response = LLMResponse(
            root_cause="LLM-derived root cause: billing retry webhook regression.",
            confidence="high",
            next_actions=["Action from LLM"],
            reasoning="The evidence points to retry webhook failures.",
        )
        client = FakeLLMClient(llm_response)

        report = run_investigation_workflow(session, run, llm_client=client)

        assert report.root_cause == llm_response.root_cause
        assert report.confidence == "high"
        assert any("Action from LLM" in action for action in report.next_actions)
        assert client.call_count == 1
        assert "MRR drop" in (client.last_prompt or "")
        assert run.trace_metadata.get("llm_provider") == "fake"
        assert run.trace_metadata.get("llm_used") is True
        assert run.prompt_tokens > 0
        assert run.completion_tokens > 0
        assert run.token_estimate == run.prompt_tokens + run.completion_tokens
        assert run.cost_estimate_usd > 0


def test_workflow_falls_back_when_llm_returns_disabled_response(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident = session.scalar(select(Incident))
        assert incident is not None
        run = AgentRun(
            id="run_llm_fallback_test",
            incident_id=incident.id,
            status="running",
            trace_id=None,
            trace_metadata={},
            input_payload={},
            token_estimate=0,
            cost_estimate_usd=0.0,
            created_at=incident.created_at,
            updated_at=incident.created_at,
        )
        session.add(run)
        session.commit()

        client = FakeLLMClient(
            LLMResponse(
                root_cause="LLM is disabled; falling back to deterministic diagnosis.",
                confidence="low",
                next_actions=[],
                reasoning="noop",
            )
        )

        report = run_investigation_workflow(session, run, llm_client=client)

        assert "retry webhook" in report.root_cause.lower()
        assert run.trace_metadata.get("llm_used") is True
        assert run.prompt_tokens > 0
