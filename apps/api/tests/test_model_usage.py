"""Phase 4: per-step model-usage tracking and trace provenance (P4-T8, P4-T10).

These are behavior tests for the product-intent guarantees in PRD §9.2 / FR-20:

* Every investigation run persists a ``model_usage`` row for the LLM-driving
  step (today: ``synthesize report``) capturing tokens, latency, and an
  *estimated* cost, even on the no-LLM fallback path, so the audit trail shows
  when the agent fell back to deterministic diagnosis.
* Every run carries a non-null trace URL (provider-neutral tracing with a local
  fallback) so a reviewer can always reach a trace link.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.persistence import AgentRunRecorder, utcnow_naive
from app.agent.service import execute_investigation_run_with_session, get_run_detail
from app.agent.workflow import run_investigation_workflow
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.core.config import get_settings
from app.db.base import Base
from app.llm import NoopLLMClient
from app.llm.schemas import LLMResponse, LLMUsage
from app.models import AgentRun, AgentRunStep, Incident, ModelUsage
from app.seed import reseed_database


class _FakeLLMClient:
    """Minimal LLM client that reports a real (used) LLM call, mirroring the
    shape in ``test_agent_llm_integration.py``. Defined locally to keep this
    test module self-contained (the used-LLM row assertions below need a
    client whose ``LLMUsage.used_llm`` is ``True``, which ``NoopLLMClient``
    never produces)."""

    provider: str = "fake"
    model: str = "fake-model"

    def __init__(self, response: LLMResponse) -> None:
        self._response = response

    def complete(self, prompt: str) -> tuple[LLMResponse, LLMUsage]:
        return self._response, LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1,
            used_llm=True,
        )


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'model_usage_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        reseed_database(session)


def _make_run(session: Session, *, run_id: str, status: str = "running") -> AgentRun:
    incident = session.scalar(select(Incident))
    assert incident is not None
    # created_at/updated_at must be "now": execute_investigation_run_with_session
    # reaps runs whose last activity is older than ACTIVE_RUN_STALE_AFTER (10m),
    # and the seeded incident is dated weeks in the past.
    now = utcnow_naive()
    run = AgentRun(
        id=run_id,
        incident_id=incident.id,
        agent_id=DEFAULT_AGENT_ID,
        agent_version_id=DEFAULT_AGENT_VERSION_ID,
        status=status,
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload={},
        token_estimate=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost_estimate_usd=0.0,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    return run


def test_run_with_llm_step_writes_model_usage_row(
    session_factory: Callable[[], Session],
) -> None:
    """The synthesize-report step persists a model_usage row whose token/cost
    fields match the run-level writes, and the step's ``model_usage_id`` points
    back at it. Covers the no-LLM fallback path (``used_llm=False``) so the audit
    trail still records that the agent fell back to deterministic diagnosis.
    """
    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_model_usage_test")

        run_investigation_workflow(session, run, llm_client=NoopLLMClient())

        # The synthesize-report step is the only LLM-driving step today.
        synth_step = session.scalar(
            select(AgentRunStep).where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.stage == "synthesize report",
            )
        )
        assert synth_step is not None, "synthesize report step was not recorded"

        usage_rows = session.scalars(
            select(ModelUsage).where(ModelUsage.run_id == run.id)
        ).all()
        assert len(usage_rows) == 1, usage_rows
        usage = usage_rows[0]

        # The step back-references its persisted usage row.
        assert synth_step.model_usage_id == usage.id
        assert usage.step_id == synth_step.id

        # Provider/model come from the (no-op) client.
        assert usage.provider == "none"
        assert usage.model == "none"

        # total_tokens is derived from prompt + completion; matches the run-level
        # writes (same LLMUsage object feeds both).
        assert usage.prompt_tokens == run.prompt_tokens
        assert usage.completion_tokens == run.completion_tokens
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
        assert usage.total_tokens == run.token_estimate

        # Noop path: the LLM was NOT used, so cost is a zero estimate and the
        # fallback reason is recorded for auditability.
        assert usage.used_llm is False
        assert usage.fallback_reason == "llm_provider=none"
        assert usage.cost_estimate_usd == 0.0
        assert usage.latency_ms >= 0
        assert usage.recorded_at is not None


def test_trace_url_present_on_every_run_local_fallback(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no external observability provider configured, every run still
    carries a non-null trace URL via the local fallback (P4-T3, FR-18). The
    provider is forced to ``local`` so the assertion is deterministic regardless
    of any Langfuse/LangSmith env vars present on the developer machine.
    """
    # Force the local trace provider so the run service's start_agent_trace()
    # call returns the local fallback deterministically.
    forced_settings = get_settings().model_copy(
        update={"observability_provider": "local"}
    )
    monkeypatch.setattr(
        "app.agent.tracing.get_settings", lambda: forced_settings
    )

    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_trace_local_test", status="queued")

        execute_investigation_run_with_session(session, run.id)

        session.refresh(run)
        assert run.trace_id is not None
        assert run.trace_url is not None
        assert run.trace_url.startswith("local://")
        assert run.trace_provider == "local"
        assert run.trace_metadata  # non-empty


def test_run_detail_surfaces_duration_and_model_usage(
    session_factory: Callable[[], Session],
) -> None:
    """Group D / P4-T9: ``get_run_detail`` (the surface behind
    ``GET /runs/{id}`` and ``GET /runs/{id}/steps``) exposes per-step
    ``duration_ms`` and the linked ``model_usage`` rows so the run-detail
    timeline can render latency and cost-as-estimate alongside each step.

    Non-LLM steps carry an empty ``model_usage`` list and a real duration; the
    LLM-driving ``synthesize report`` step carries exactly one usage row whose
    token totals match the run-level writes.
    """
    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_detail_usage_test")

        run_investigation_workflow(session, run, llm_client=NoopLLMClient())

        detail = get_run_detail(session, run.id)

    steps = {step.stage: step for step in detail.steps}
    synth = steps["synthesize report"]
    assert synth.status == "succeeded"
    # duration_ms is derived from started_at/completed_at for completed steps.
    assert synth.duration_ms is not None
    assert synth.duration_ms >= 0

    assert len(synth.model_usage) == 1
    usage = synth.model_usage[0]
    assert usage.provider == "none"
    assert usage.model == "none"
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
    assert usage.used_llm is False
    assert usage.cost_estimate_usd == 0.0
    assert usage.step_id == synth.id

    # Non-LLM steps carry no model_usage rows but still report a duration.
    intake = steps["intake"]
    assert intake.model_usage == []
    assert intake.duration_ms is not None
    assert intake.duration_ms >= 0


def test_run_with_real_llm_writes_used_llm_model_usage_row(
    session_factory: Callable[[], Session],
) -> None:
    """The ``used_llm=True`` branch of FR-20 / PRD §9.2: when the LLM was
    actually used, the persisted ``ModelUsage`` row records a positive
    estimated cost, no fallback reason, and token totals matching prompt +
    completion. The NoopLLMClient suite covers ``used_llm=False`` (zero cost,
    fallback reason); this guards the distinct cost-estimation branch in
    ``_persist_model_usage`` that a Noop-only suite would miss.
    """
    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_model_usage_llm_test")
        # Root cause is one the evidence supports, so diagnose_with_llm_or_fallback
        # ACCEPTS the LLM answer (used_llm=True, fallback_reason=None) rather than
        # rejecting it as unsupported and falling back to deterministic diagnosis.
        # Mirrors the accepted-LLM case in test_agent_llm_integration.py.
        response = LLMResponse(
            root_cause="LLM-derived root cause: billing retry webhook regression.",
            confidence="high",
            next_actions=["Action from LLM"],
            reasoning="The evidence points to retry webhook failures.",
        )
        report = run_investigation_workflow(
            session, run, llm_client=_FakeLLMClient(response)
        )

        # The LLM diagnosis was accepted (not rejected as unsupported).
        assert report.root_cause == response.root_cause

        synth_step = session.scalar(
            select(AgentRunStep).where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.stage == "synthesize report",
            )
        )
        assert synth_step is not None, "synthesize report step was not recorded"

        usage_rows = session.scalars(
            select(ModelUsage).where(ModelUsage.run_id == run.id)
        ).all()
        assert len(usage_rows) == 1, usage_rows
        usage = usage_rows[0]

        # The step back-references its persisted usage row.
        assert synth_step.model_usage_id == usage.id
        assert usage.step_id == synth_step.id

        # Real-LLM path: provider/model come from the client.
        assert usage.provider == "fake"
        assert usage.model == "fake-model"

        # used_llm=True -> positive estimated cost (unknown model falls back
        # to DEFAULT_PRICING in llm/pricing.py) and NO fallback reason (the
        # LLM answer was accepted, so no deterministic fallback was taken).
        assert usage.used_llm is True
        assert usage.fallback_reason is None
        assert usage.cost_estimate_usd > 0.0

        # total_tokens is derived from prompt + completion; per-step totals
        # match the run-level writes (the same LLMUsage object fed both).
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
        assert usage.prompt_tokens == run.prompt_tokens
        assert usage.completion_tokens == run.completion_tokens
        assert usage.latency_ms >= 0
        assert usage.recorded_at is not None


def test_model_usage_persistence_failure_does_not_abort_run(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reliability: ModelUsage persistence is best-effort. If the observability
    write fails at flush time (constraint violation, serialization error), the
    synthesize-report step still completes as ``succeeded``, the investigation
    is not aborted, AND the run-level token/cost/trace_metadata writes survive
    — telemetry must never gate or corrupt the investigation outcome.

    Uses ``_FakeLLMClient`` (non-zero tokens, used_llm=True) so the run-level
    writes are observably non-zero; a flush-time NOT NULL violation is injected
    into the isolated usage session so the realistic failure path (commit-time
    IntegrityError, not a pre-flush raise) is exercised. The main session has
    already committed the step + run-level writes before the usage session
    opens, so those writes are durable and untouched by the usage rollback.
    """
    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_model_usage_fail_test")

        def add_bad_row(
            self: AgentRunRecorder,
            session: Session,
            *,
            run_id: str,
            step_id: str,
            usages: list[LLMUsage],
        ) -> str | None:
            # Add a ModelUsage that violates the NOT NULL constraint on
            # ``provider`` so the usage_session commit raises IntegrityError at
            # flush time — the realistic failure mode (not a pre-flush raise).
            session.add(
                ModelUsage(
                    id="mu_bad",
                    run_id=run_id,
                    step_id=step_id,
                    provider=None,  # NOT NULL violation -> commit-time IntegrityError
                    model="fake-model",
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    cost_estimate_usd=0.0,
                    latency_ms=0,
                    used_llm=False,
                    recorded_at=utcnow_naive(),
                )
            )
            return "mu_bad"

        monkeypatch.setattr(AgentRunRecorder, "_persist_model_usage", add_bad_row)

        response = LLMResponse(
            root_cause="LLM-derived root cause: billing retry webhook regression.",
            confidence="high",
            next_actions=["Action from LLM"],
            reasoning="The evidence points to retry webhook failures.",
        )
        report = run_investigation_workflow(
            session, run, llm_client=_FakeLLMClient(response)
        )

        # The report was synthesized despite the usage-write failure.
        assert report is not None
        assert report.root_cause == response.root_cause

        # The run-level token/cost writes SURVIVED the usage-write failure
        # (the main session committed them before the isolated usage session
        # opened, so the usage rollback cannot touch them). This is the
        # load-bearing invariant: a telemetry failure must not zero out the
        # audit trail.
        session.refresh(run)
        assert run.prompt_tokens == 10
        assert run.completion_tokens == 5
        assert run.token_estimate == 15
        assert run.cost_estimate_usd > 0.0
        assert run.trace_metadata.get("llm_used") is True

        synth_step = session.scalar(
            select(AgentRunStep).where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.stage == "synthesize report",
            )
        )
        assert synth_step is not None
        # The step completed as succeeded, not failed/blocked.
        assert synth_step.status == "succeeded"
        # No usage rows were persisted (the bad row was rolled back in the
        # isolated usage session).
        usage_rows = session.scalars(
            select(ModelUsage).where(ModelUsage.run_id == run.id)
        ).all()
        assert usage_rows == []
        # And the step carries no stale model_usage_id back-pointer: the usage
        # session rolled back, so the link was never persisted.
        assert synth_step.model_usage_id is None


def test_step_usage_link_survives_service_layer_finalization(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step<->usage back-pointer must survive the service layer's
    success-state finalization, not just the direct workflow call.

    The other usage tests invoke ``run_investigation_workflow`` directly,
    bypassing ``execute_investigation_run_with_session``. That service path
    performs several commits AFTER the recorder's usage session already wrote
    ``step.model_usage_id`` in an isolated session:

      * the success-state conditional ``UPDATE(AgentRun)`` + ``session.commit()``
        (the run is marked ``succeeded``),
      * ``_propose_report_actions`` + its own ``session.commit()`` (adds an
        action-proposal step and commits again).

    Because ``SessionLocal`` defaults to ``expire_on_commit=True``, the main
    session's step instance is expired (not dirty) after the recorder's commit,
    so none of these later commits re-flush the step and clobber the usage
    session's link. This test pins that load-bearing invariant end-to-end: a
    future refactor that adds a step write to the finalization path, or that
    disables ``expire_on_commit``, must not silently null the audit
    back-pointer.
    """
    # Force the local trace provider for determinism (mirrors
    # test_trace_url_present_on_every_run_local_fallback).
    forced_settings = get_settings().model_copy(
        update={"observability_provider": "local"}
    )
    monkeypatch.setattr("app.agent.tracing.get_settings", lambda: forced_settings)

    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_usage_link_e2e_test", status="queued")

        # Drive the FULL service path: claim -> workflow (with the default
        # version's NoopLLMClient, which still records a used_llm=False usage
        # row) -> success finalization -> action proposal -> final commit.
        detail = execute_investigation_run_with_session(session, run.id)

        session.refresh(run)
        # The run reached succeeded through the service layer (not just the
        # direct workflow), exercising every post-recorder commit.
        assert run.status == "succeeded"
        assert detail is not None

        synth_step = session.scalar(
            select(AgentRunStep).where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.stage == "synthesize report",
            )
        )
        assert synth_step is not None
        assert synth_step.status == "succeeded"

        usage_rows = session.scalars(
            select(ModelUsage).where(ModelUsage.run_id == run.id)
        ).all()
        assert len(usage_rows) == 1, usage_rows
        usage = usage_rows[0]

        # THE load-bearing assertion: the usage session's back-pointer survived
        # every service-layer commit. A null here would mean a later commit
        # clobbered the isolated usage write.
        assert synth_step.model_usage_id == usage.id
        assert usage.step_id == synth_step.id
        # NoopLLMClient path: the row is recorded but marks the fallback.
        assert usage.used_llm is False
        assert usage.fallback_reason == "llm_provider=none"


def test_rejected_llm_diagnosis_persists_used_llm_with_fallback_reason(
    session_factory: Callable[[], Session],
) -> None:
    """The distinct ``used_llm=True`` AND ``fallback_reason`` set audit-trail
    state: the LLM was called (tokens consumed, cost incurred) but its diagnosis
    was rejected as unsupported by evidence, so the agent fell back to the
    deterministic diagnosis (PRD §9.2).

    This is the one state where cost and a fallback reason coexist. The cost
    branch in ``_persist_model_usage`` keys on ``used_llm`` (NOT
    ``fallback_reason``), so cost stays positive even though the answer was
    discarded. Without this test a future refactor conflating "fallback_reason
    set" with "zero cost" (e.g. ``if usage.used_llm and not fallback_reason``)
    would pass every other test while silently zeroing the audit cost for the
    rejected-LLM case.
    """
    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_model_usage_rejected_llm_test")
        # An UNSUPPORTED root cause: not present in the seeded evidence, so
        # diagnose_with_llm_or_fallback REJECTS the LLM answer (used_llm=True,
        # fallback_reason set) and returns the deterministic diagnosis. Mirrors
        # test_diagnose_with_llm_or_fallback_rejects_unsupported_specific_root_cause.
        response = LLMResponse(
            root_cause="Pricing discounts were misconfigured during renewal.",
            confidence="high",
            next_actions=["Audit pricing discounts."],
            reasoning="This conclusion is not present in the retrieved evidence.",
        )
        report = run_investigation_workflow(
            session, run, llm_client=_FakeLLMClient(response)
        )

        # The LLM answer was rejected -> the deterministic diagnosis is used.
        assert report.root_cause != response.root_cause
        assert report.root_cause == (
            "Billing retry webhook regression suppressed second charge attempts."
        )

        synth_step = session.scalar(
            select(AgentRunStep).where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.stage == "synthesize report",
            )
        )
        assert synth_step is not None

        usage_rows = session.scalars(
            select(ModelUsage).where(ModelUsage.run_id == run.id)
        ).all()
        assert len(usage_rows) == 1, usage_rows
        usage = usage_rows[0]

        # The step back-references its persisted usage row.
        assert synth_step.model_usage_id == usage.id
        assert usage.step_id == synth_step.id

        # THE load-bearing assertions for this distinct state: the LLM WAS used
        # (tokens consumed, positive cost) AND the answer was discarded
        # (fallback_reason records the deterministic fallback).
        assert usage.used_llm is True
        assert usage.fallback_reason == (
            "unsupported_llm_diagnosis: deterministic_fallback"
        )
        assert usage.cost_estimate_usd > 0.0
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens

        # The run-level token/cost writes reflect that the LLM was billed.
        session.refresh(run)
        assert run.prompt_tokens == 10
        assert run.completion_tokens == 5
        assert run.cost_estimate_usd > 0.0
        assert run.trace_metadata.get("llm_used") is True


def test_usage_session_acquisition_failure_does_not_abort_run(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The best-effort invariant under a usage-SESSION ACQUISITION failure: if
    opening the isolated usage session (``Session(bind=...)`` / ``get_bind()``)
    raises — pool exhaustion, connection error — the run must still complete as
    succeeded with its run-level audit trail intact and no usage rows.

    This pins the HIGH fix from review pass #3: the ``Session`` acquisition lives
    INSIDE the try, so an acquisition failure is swallowed exactly like a
    row-write failure rather than propagating to ``_fail_running_run`` and
    marking a fully-succeeded run failed. ``app.agent.persistence.Session`` is
    the only runtime CALL site of that name (annotations are strings under
    ``from __future__ import annotations``), so patching it is surgical and does
    not affect SQLAlchemy's own internal flush/get_bind.
    """
    _seed(session_factory)
    with session_factory() as session:
        run = _make_run(session, run_id="run_model_usage_acq_fail_test")

        def raising_session(*_args, **_kwargs):
            raise OSError("simulated pool exhaustion / connection refused")

        monkeypatch.setattr("app.agent.persistence.Session", raising_session)

        response = LLMResponse(
            root_cause="LLM-derived root cause: billing retry webhook regression.",
            confidence="high",
            next_actions=["Action from LLM"],
            reasoning="The evidence points to retry webhook failures.",
        )
        report = run_investigation_workflow(
            session, run, llm_client=_FakeLLMClient(response)
        )

        # The report was synthesized despite the usage-session acquisition
        # failure — the run did NOT route to _fail_running_run.
        assert report is not None
        assert report.root_cause == response.root_cause

        # The run-level token/cost writes SURVIVED (the main session committed
        # them before the usage session was ever opened).
        session.refresh(run)
        assert run.prompt_tokens == 10
        assert run.completion_tokens == 5
        assert run.token_estimate == 15
        assert run.cost_estimate_usd > 0.0
        assert run.trace_metadata.get("llm_used") is True

        synth_step = session.scalar(
            select(AgentRunStep).where(
                AgentRunStep.run_id == run.id,
                AgentRunStep.stage == "synthesize report",
            )
        )
        assert synth_step is not None
        # The step completed as succeeded, not failed/blocked.
        assert synth_step.status == "succeeded"
        # No usage rows could be persisted (the usage session never opened).
        usage_rows = session.scalars(
            select(ModelUsage).where(ModelUsage.run_id == run.id)
        ).all()
        assert usage_rows == []
        # And the step carries no model_usage_id back-pointer.
        assert synth_step.model_usage_id is None
