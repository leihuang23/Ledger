from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, AgentRunStep

T = TypeVar("T")


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentRunRecorder:
    def __init__(self, session: Session, run: AgentRun) -> None:
        self.session = session
        self.run = run
        self._next_sequence: int | None = None

    def record(
        self,
        *,
        stage: str,
        inputs: object,
        action: Callable[[], T],
        tool_name: str | None = None,
    ) -> T:
        step = self._start_step(stage=stage, tool_name=tool_name, inputs=inputs)
        try:
            output = action()
        except Exception as exc:
            self._fail_step(step.id, exc)
            raise

        self._complete_step(step.id, output)
        return output

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
        self.session.commit()
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

    def _complete_step(self, step_id: str, output: object) -> None:
        now = utcnow_naive()
        step = self.session.get(AgentRunStep, step_id)
        if step is None:
            raise RuntimeError(f"Agent run step disappeared: {step_id}")
        step.status = "succeeded"
        step.outputs = jsonable_encoder(output)
        step.completed_at = now
        self.session.commit()

    def _fail_step(self, step_id: str, exc: Exception) -> None:
        self.session.rollback()
        now = utcnow_naive()
        step = self.session.get(AgentRunStep, step_id)
        if step is not None:
            step.status = "failed"
            step.error = str(exc)
            step.completed_at = now
            self.session.commit()
