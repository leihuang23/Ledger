from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.tracing import AgentTraceHandle
from app.llm import estimate_cost_usd
from app.llm.schemas import LLMUsage
from app.models import AgentRun, AgentRunStep, ModelUsage

logger = logging.getLogger(__name__)

T = TypeVar("T")


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentRunRecorder:
    _COMMIT_EVERY = 5

    def __init__(
        self, session: Session, run: AgentRun, trace: AgentTraceHandle | None = None
    ) -> None:
        self.session = session
        self.run = run
        self.trace = trace
        self._next_sequence: int | None = None
        self._steps_since_commit = 0

    def record(
        self,
        *,
        stage: str,
        inputs: object,
        action: Callable[[], T],
        tool_name: str | None = None,
        model_usage: list[LLMUsage] | None = None,
    ) -> T:
        """Record one workflow step: run ``action`` inside a SAVEPOINT, then
        persist its outputs and (optionally) per-step LLM usage.

        ``model_usage`` is a "usage_box": a caller-declared list that the action
        populates by appending the ``LLMUsage`` it captured (see
        ``synthesize_report_node``). When non-None, ``_complete_step`` commits the
        main session (so the step + run-level token/cost/trace_metadata writes
        become durable) and then persists ``ModelUsage`` rows best-effort in an
        ISOLATED session. This means passing ``model_usage`` has a transactional
        side effect (a main-session commit). Only the terminal LLM-driving step
        (``synthesize report``) should pass it: a non-terminal caller would split
        its transaction boundary unexpectedly. Today only that node passes a
        non-None ``model_usage``.
        """
        step = self._start_step(stage=stage, tool_name=tool_name, inputs=inputs)
        try:
            # Wrap the action in a SAVEPOINT so that DB changes made by a
            # failing action are rolled back without discarding previously
            # flushed (or committed) steps in the outer transaction. Some
            # action callables (e.g. propose_actions_for_report) call
            # session.commit() internally, which releases the SAVEPOINT; guard
            # with is_active so we don't touch a closed transaction.
            savepoint = self.session.begin_nested()
            try:
                if self.trace is not None:
                    output = self.trace.record_child(
                        name=tool_name or stage,
                        run_type="tool" if tool_name else "chain",
                        inputs=inputs,
                        action=action,
                    )
                else:
                    output = action()
                if savepoint.is_active:
                    savepoint.commit()
            except Exception:
                if savepoint.is_active:
                    savepoint.rollback()
                raise
        except Exception as exc:
            self._fail_step(step.id, exc)
            raise

        # ``model_usage`` is a "usage_box": a caller-declared list that the
        # action populates by appending the LLMUsage it captured (see
        # synthesize_report_node). By the time we get here the action has
        # returned, so the box holds the usage to persist as ModelUsage rows.
        # Kept out of ``step.outputs`` (which stays the report dict) and out of
        # the run-level writes (which _synthesize_report still owns).
        self._complete_step(step.id, output, model_usage=model_usage)
        return output

    def record_blocked(
        self,
        *,
        stage: str,
        tool_name: str | None,
        inputs: object,
        blocked_reason: str,
        fallback_output: object,
    ) -> None:
        """Record a tool call that was NOT dispatched because the agent version's
        permission policy blocked it (PRD FR-7, AC-2.4).

        Unlike ``record``, this does not raise and does not run the tool's
        action. It persists a step with ``status="blocked"`` and the
        ``blocked_reason``, while still storing ``fallback_output`` in
        ``outputs`` so downstream report synthesis and eval regression can
        consume the degraded-evidence payload (e.g. ``tool_disabled: True``).

        Committed immediately so the blocked step is visible in the run history
        without waiting for a batch (mirrors ``_fail_step``).
        """
        step = self._start_step(stage=stage, tool_name=tool_name, inputs=inputs)
        self._block_step(step.id, blocked_reason, fallback_output)

    def _touch_run_heartbeat(self) -> None:
        self.run.updated_at = utcnow_naive()

    def _start_step(
        self, *, stage: str, tool_name: str | None, inputs: object
    ) -> AgentRunStep:
        now = utcnow_naive()
        sequence = self._allocate_sequence()
        step = AgentRunStep(
            id=f"step_{uuid4().hex[:16]}",
            run_id=self.run.id,
            sequence=sequence,
            stage=stage,
            tool_name=tool_name,
            status="running",
            inputs=jsonable_encoder(inputs),
            outputs=None,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
        )
        self.session.add(step)
        self._touch_run_heartbeat()
        self.session.flush()
        return step

    def _allocate_sequence(self) -> int:
        if self._next_sequence is None:
            current_max = int(
                self.session.scalar(
                    select(func.coalesce(func.max(AgentRunStep.sequence), 0)).where(
                        AgentRunStep.run_id == self.run.id
                    )
                )
                or 0
            )
            self._next_sequence = current_max + 1

        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def _complete_step(
        self,
        step_id: str,
        output: object,
        *,
        model_usage: list[LLMUsage] | None = None,
    ) -> None:
        now = utcnow_naive()
        step = self.session.get(AgentRunStep, step_id)
        if step is None:
            raise RuntimeError(f"Agent run step disappeared: {step_id}")
        # Set the step's success fields FIRST, then flush. ModelUsage is
        # supplementary observability telemetry (PRD §9.2 / FR-20): a failure
        # there must never abort the step or the run, and must never revert the
        # run-level token/cost/trace_metadata writes _synthesize_report set as
        # pending. Flushing here lands those writes in the DB before any usage
        # persistence runs, so the usage path can neither co-flush nor roll
        # them back. Mirrors the tracing provider's swallow-errors posture.
        step.status = "succeeded"
        step.outputs = jsonable_encoder(output)
        step.completed_at = now
        self._touch_run_heartbeat()
        self.session.flush()
        if model_usage:
            # Persist ModelUsage rows in an ISOLATED session so a usage-write
            # failure can never poison the main transaction. SQLite's pysqlite
            # cannot recover from a flush IntegrityError inside a SAVEPOINT
            # without a full session.rollback(), so a savepoint is not a
            # cross-DB solution. _persist_model_usage_safe commits the main
            # session first (making the step + run-level writes durable so the
            # usage FK resolves and the audit trail survives), then writes the
            # rows + step back-pointer in a short-lived separate session.
            self._persist_model_usage_safe(step.id, model_usage)
        else:
            self._maybe_commit()

    def _persist_model_usage_safe(
        self, step_id: str, usages: list[LLMUsage]
    ) -> None:
        """Persist ModelUsage rows best-effort in an isolated session.

        Observability telemetry must never gate or corrupt the investigation
        outcome (PRD: the LLM synthesizes, it does not gate persistence). The
        main session is committed first so the step row (satisfying the
        ``model_usage.step_id`` FK) and the run-level token/cost/trace_metadata
        writes become durable. A separate short-lived session then writes the
        usage rows and the step back-pointer; any failure there is rolled back
        and logged, leaving the step to stand as succeeded with no usage rows.
        """
        run_id = self.run.id
        # Commit the main session: makes the step + run-level writes durable
        # (the usage session needs the step row committed to resolve the FK)
        # and matches the recorder's existing commit cadence (every 5 steps via
        # _maybe_commit). The synthesize step is the final node, so this commit
        # finalizes the run-level audit trail before the service layer's own
        # success-state commit.
        self.session.commit()
        self._steps_since_commit = 0

        usage_session: Session | None = None
        try:
            # _persist_model_usage writes the rows AND sets the step back-pointer
            # on the usage_session's own step instance, so the link is committed
            # atomically with the rows. We do NOT propagate the link back to the
            # main session: it is expired after the commit above, and a pending
            # write there could overwrite this link on a later main-session
            # commit. The link is read back fresh by get_run_detail and direct
            # queries.
            #
            # The Session/bind acquisition lives INSIDE the try: a pool-exhaustion
            # or connection error here must be swallowed exactly like a row-write
            # failure, because the main session has already committed the
            # succeeded step + run-level audit trail. Letting it propagate would
            # route to _fail_running_run and mark a fully-succeeded run failed
            # purely because best-effort telemetry could not be opened — a direct
            # violation of the best-effort invariant (PRD §9.2 / FR-20).
            usage_session = Session(bind=self.session.get_bind())
            self._persist_model_usage(
                usage_session, run_id=run_id, step_id=step_id, usages=usages
            )
            usage_session.commit()
        except Exception as exc:
            if usage_session is not None:
                usage_session.rollback()
            logger.warning(
                "model_usage persistence failed for run=%s step=%s "
                "(attempted=%d rows); step will complete without usage rows: %r",
                run_id,
                step_id,
                len(usages),
                exc,
            )
        finally:
            if usage_session is not None:
                usage_session.close()

    def _persist_model_usage(
        self,
        session: Session,
        *,
        run_id: str,
        step_id: str,
        usages: list[LLMUsage],
    ) -> str | None:
        """Persist one ``ModelUsage`` row per LLMUsage on ``session`` and link
        the step back to the first row (PRD §9.2 / FR-20).

        Cost is estimated only when the LLM was actually used; the no-LLM
        fallback path records a zero-cost row with the fallback reason so the
        audit trail still shows when the agent fell back to deterministic
        diagnosis. Token totals are derived from prompt + completion, matching
        the run-level writes (same LLMUsage feeds both). Returns the first row
        id (or ``None`` if no usages were persisted).
        """
        first_id: str | None = None
        step = session.get(AgentRunStep, step_id)
        if step is None:
            raise RuntimeError(f"Agent run step disappeared: {step_id}")
        for usage in usages:
            row_id = f"mu_{uuid4().hex[:16]}"
            total_tokens = usage.prompt_tokens + usage.completion_tokens
            cost_estimate_usd = (
                estimate_cost_usd(
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    model=usage.model,
                )
                if usage.used_llm
                else 0.0
            )
            session.add(
                ModelUsage(
                    id=row_id,
                    run_id=run_id,
                    step_id=step_id,
                    provider=usage.provider,
                    model=usage.model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=total_tokens,
                    cost_estimate_usd=cost_estimate_usd,
                    latency_ms=usage.latency_ms,
                    used_llm=usage.used_llm,
                    fallback_reason=usage.fallback_reason,
                    recorded_at=utcnow_naive(),
                )
            )
            if first_id is None:
                first_id = row_id
        if first_id is not None:
            step.model_usage_id = first_id
        return first_id

    def _maybe_commit(self) -> None:
        self._steps_since_commit += 1
        if self._steps_since_commit >= self._COMMIT_EVERY:
            self.session.commit()
            self._steps_since_commit = 0

    def _fail_step(self, step_id: str, exc: Exception) -> None:
        # The SAVEPOINT in record() already rolled back the action's changes.
        # The step row was flushed before the SAVEPOINT, so it survives and
        # we can update it in place. Always commit failures immediately so
        # they are visible in the run history without waiting for a batch.
        now = utcnow_naive()
        step = self.session.get(AgentRunStep, step_id)
        if step is not None:
            step.status = "failed"
            step.error = str(exc)
            step.completed_at = now
            self._touch_run_heartbeat()
            self.session.commit()
            self._steps_since_commit = 0

    def _block_step(
        self, step_id: str, blocked_reason: str, fallback_output: object
    ) -> None:
        # Finalize a permission-blocked tool step (PRD FR-7). The step row was
        # flushed in _start_step; update it in place and commit immediately so
        # the blocked step is visible without waiting for a batch.
        now = utcnow_naive()
        step = self.session.get(AgentRunStep, step_id)
        if step is None:
            raise RuntimeError(f"Agent run step disappeared: {step_id}")
        step.status = "blocked"
        step.blocked_reason = blocked_reason
        step.outputs = jsonable_encoder(fallback_output)
        step.completed_at = now
        self._touch_run_heartbeat()
        self.session.commit()
        self._steps_since_commit = 0
