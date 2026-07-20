from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, contains_eager, selectinload

from app.agent.persistence import utcnow_naive
from app.agent.schemas import InvestigationReport
from app.approvals.schemas import (
    ActionAuditEventRead,
    ApprovalDecisionCreate,
    ApprovalRequestRead,
    ApprovalRequestSummary,
    ApprovalStatus,
    MockActionCreate,
    MockActionRead,
    RiskLevel,
)
from app.cache import Cache
from app.models import ActionAuditEvent, AgentRun, ApprovalRequest, MockAction
from app.runs.lifecycle import validate_transition

HIGH_RISK_ACTION_TYPES = {"draft_customer_email", "update_account_note"}
REPORT_ACTION_TYPES = (
    "draft_slack_message",
    "draft_customer_email",
    "create_task",
    "update_account_note",
)
ACTION_ACTOR = "agent"
# Actions created through the operator API (POST /mock-actions) are attributed
# to the operator so the audit trail can tell human-injected actions apart
# from agent-proposed ones. Client-supplied actor fields are never trusted.
OPERATOR_ACTOR = "operator"
APPROVER_ACTOR = "demo-approver"


class RunStateConflictError(Exception):
    """An operator mock action was rejected because of the run lifecycle state.

    Mapped to HTTP 409 by the router, mirroring how illegal run transitions
    surface. Distinct from ``ValueError`` so payload-contract violations keep
    their 422 mapping.
    """


PAYLOAD_CONTRACTS = {
    "draft_slack_message": {
        "required": {"message"},
        "allowed": {"message", "source", "evidence_ids"},
    },
    "draft_customer_email": {
        "required": {"subject", "body"},
        "allowed": {"subject", "body", "account_ids", "account_names", "evidence_ids"},
    },
    "create_task": {
        "required": {"task_title"},
        "allowed": {"task_title", "root_cause", "account_ids", "evidence_ids"},
    },
    "update_account_note": {
        "required": {"note"},
        "allowed": {"note", "account_ids", "evidence_ids"},
    },
}


def create_mock_action(
    session: Session,
    payload: MockActionCreate,
    *,
    actor: str = OPERATOR_ACTOR,
) -> MockActionRead:
    """Operator API entry point: create a mock action and gate high-risk actions.

    This is the only path that enforces the operator run-state policy
    (``_validate_operator_action_run_state``). The agent's own action creation
    bypasses it on purpose: the executor proposes actions via
    ``propose_actions_for_report`` (straight to ``_create_mock_action_record``)
    while the run is still ``running``, and the registry tool bindings below
    (``create_low_risk_mock_action`` / ``request_high_risk_approval``) are
    agent-actor entries that must stay callable mid-run. Nothing in the
    runtime dispatches those bindings through this operator entry point.

    Operator-created actions are attributed to ``OPERATOR_ACTOR`` in the audit
    trail; the governed tool wrappers below pass ``ACTION_ACTOR`` instead.
    """
    # Lock the run row so the state check serializes with approval decisions
    # and executor finalization (both take the same lock).
    run = _lock_approval_run(session, payload.run_id)
    _validate_operator_action_run_state(run, payload.action_type)

    action = _create_mock_action_record(session, payload, actor=actor)
    session.commit()
    return get_mock_action(session, action.id)


def _validate_operator_action_run_state(run: AgentRun, action_type: str) -> None:
    """Run-state policy for operator-injected mock actions (POST /mock-actions).

    - ``queued``/``running``: rejected. While a run is in flight the agent owns
      action creation; an injected high-risk action would inflate
      finalization's ``pending_approval_count`` and drag the run into
      ``waiting_for_approval``.
    - ``waiting_for_approval``: allowed. The approval checkpoint is the
      operator's legitimate window; new high-risk actions join the pending
      queue and the run resumes only once every pending approval is decided.
    - ``succeeded``/``failed`` (terminal): product decision - low-risk
      follow-ups stay allowed because they execute immediately and can never
      create a pending approval, so the "no pending approvals on a terminal
      run" invariant holds. High-risk actions are rejected: a pending approval
      on a finished run could never gate anything and would leave the approval
      queue pointing at a run that can no longer resume.
    """
    if run.status in ("queued", "running"):
        raise RunStateConflictError(
            f"Cannot add operator actions to run {run.id} while it is "
            f"{run.status!r}; the agent owns action creation until the run "
            "reaches the approval checkpoint or a terminal state."
        )
    if run.status in ("succeeded", "failed") and (
        classify_action_risk(action_type) == "high"
    ):
        raise RunStateConflictError(
            f"Cannot request approval for run {run.id}: the run is "
            f"{run.status!r} (terminal), so a pending approval could never be "
            "resolved by it. Low-risk follow-up actions remain allowed."
        )


def create_low_risk_mock_action(
    session: Session, payload: MockActionCreate
) -> MockActionRead:
    """Registered write tool that cannot create approval requests."""
    if classify_action_risk(payload.action_type) == "high":
        raise ValueError(
            f"{payload.action_type} requires the request_approval tool"
        )
    return _create_tool_mock_action(session, payload)


def request_high_risk_approval(
    session: Session, payload: MockActionCreate
) -> MockActionRead:
    """Registered approval tool that accepts only high-risk action drafts."""
    if classify_action_risk(payload.action_type) != "high":
        raise ValueError(
            f"{payload.action_type} does not require an approval request"
        )
    return _create_tool_mock_action(session, payload)


def _create_tool_mock_action(
    session: Session, payload: MockActionCreate
) -> MockActionRead:
    """Agent-actor tool entry: mirrors ``create_mock_action`` without the
    operator run-state guard so mid-run (``running``) dispatch stays legal."""
    if session.get(AgentRun, payload.run_id) is None:
        raise LookupError(f"Unknown agent run id: {payload.run_id}")
    action = _create_mock_action_record(session, payload, actor=ACTION_ACTOR)
    session.commit()
    return get_mock_action(session, action.id)


def _create_mock_action_record(
    session: Session, payload: MockActionCreate, *, actor: str
) -> MockAction:
    validate_action_payload(payload)
    now = utcnow_naive()
    risk_level = classify_action_risk(payload.action_type)
    action = MockAction(
        id=f"act_{uuid4().hex[:16]}",
        run_id=payload.run_id,
        action_type=payload.action_type,
        risk_level=risk_level,
        status="pending_approval" if risk_level == "high" else "executed",
        title=payload.title,
        description=payload.description,
        target=payload.target,
        payload=payload.payload,
        created_by=actor,
        created_at=now,
        updated_at=now,
        executed_at=None if risk_level == "high" else now,
    )
    session.add(action)
    session.flush()

    approval_request: ApprovalRequest | None = None
    if risk_level == "high":
        approval_request = ApprovalRequest(
            id=f"apr_{uuid4().hex[:16]}",
            run_id=payload.run_id,
            action_id=action.id,
            status="pending",
            risk_level=risk_level,
            reason=approval_reason(action),
            requested_by=actor,
            decided_by=None,
            decision_notes=None,
            created_at=now,
            decided_at=None,
        )
        session.add(approval_request)
        session.flush()

    _record_audit_event(
        session,
        run_id=payload.run_id,
        action_id=action.id,
        approval_request_id=approval_request.id if approval_request else None,
        event_type="proposed",
        actor=actor,
        notes=f"Proposed {payload.action_type.replace('_', ' ')}.",
        metadata={"risk_level": risk_level, "action_type": payload.action_type},
        created_at=now,
    )

    if risk_level == "low":
        _record_audit_event(
            session,
            run_id=payload.run_id,
            action_id=action.id,
            approval_request_id=None,
            event_type="executed",
            actor="mock-action-runner",
            notes="Low-risk mock action executed immediately.",
            metadata={"risk_level": risk_level, "action_type": payload.action_type},
            created_at=now,
        )

    return action


def propose_actions_for_report(
    session: Session,
    *,
    run_id: str,
    report: InvestigationReport,
    allow_approval_requests: bool = True,
    action_types: set[str] | frozenset[str] | None = None,
) -> list[MockActionRead]:
    existing_actions = session.scalars(
        select(MockAction).where(MockAction.run_id == run_id)
    ).all()
    existing_action_types = {action.action_type for action in existing_actions}
    requested_action_types = set(action_types or REPORT_ACTION_TYPES)
    unknown_action_types = requested_action_types - set(REPORT_ACTION_TYPES)
    if unknown_action_types:
        raise ValueError(
            "Unknown report action types: " + ", ".join(sorted(unknown_action_types))
        )
    expected_action_types = {
        action_type
        for action_type in requested_action_types
        if allow_approval_requests or action_type not in HIGH_RISK_ACTION_TYPES
    }
    if expected_action_types.issubset(existing_action_types):
        return list_mock_actions_for_run(session, run_id)

    affected_account_ids = [account.account_id for account in report.affected_accounts]
    affected_names = [account.account_name for account in report.affected_accounts]
    evidence_ids = [evidence.reference_id for evidence in report.cited_evidence[:5]]
    affected_label = (
        f"{len(affected_account_ids)} affected accounts"
        if affected_account_ids
        else "unconfirmed affected accounts"
    )
    first_action = report.next_actions[0] if report.next_actions else report.root_cause
    customer_subject = customer_email_subject(report)
    customer_body = customer_email_body(report)
    action_payloads = [
        MockActionCreate(
            run_id=run_id,
            action_type="draft_slack_message",
            title="Draft internal incident update",
            description="Prepare an internal Slack update that cites the investigation result.",
            target="#revenue-ops",
            payload={
                "message": (
                    f"{report.root_cause} Confidence: {report.confidence}. "
                    f"Scope: {affected_label}."
                ),
                "source": "investigation_report",
                "evidence_ids": evidence_ids,
            },
        ),
        MockActionCreate(
            run_id=run_id,
            action_type="draft_customer_email",
            title="Draft customer follow-up email",
            description="Prepare customer-facing follow-up for accounts named by the report.",
            target="affected billing contacts",
            payload={
                "subject": customer_subject,
                "body": customer_body,
                "account_ids": affected_account_ids,
                "account_names": affected_names,
                "evidence_ids": evidence_ids,
            },
        ),
        MockActionCreate(
            run_id=run_id,
            action_type="create_task",
            title="Create recovery task",
            description="Create a mock operational task from the report's first recommendation.",
            target="revenue operations backlog",
            payload={
                "task_title": first_action,
                "root_cause": report.root_cause,
                "account_ids": affected_account_ids,
                "evidence_ids": evidence_ids,
            },
        ),
        MockActionCreate(
            run_id=run_id,
            action_type="update_account_note",
            title="Update affected account note",
            description="Prepare a mock account-note update grounded in cited evidence.",
            target="affected account records",
            payload={
                "note": report.summary,
                "account_ids": affected_account_ids,
                "evidence_ids": evidence_ids,
            },
        ),
    ]

    for payload in action_payloads:
        if payload.action_type not in expected_action_types:
            continue
        if (
            not allow_approval_requests
            and payload.action_type in HIGH_RISK_ACTION_TYPES
        ):
            continue
        if payload.action_type not in existing_action_types:
            _create_mock_action_record(session, payload, actor=ACTION_ACTOR)
            existing_action_types.add(payload.action_type)

    session.commit()
    return list_mock_actions_for_run(session, run_id)


def list_mock_actions_for_run(session: Session, run_id: str) -> list[MockActionRead]:
    actions = session.scalars(
        select(MockAction)
        .where(MockAction.run_id == run_id)
        .order_by(MockAction.created_at, MockAction.id)
    ).all()
    return [mock_action_to_read(action) for action in actions]


def get_mock_action(session: Session, action_id: str) -> MockActionRead:
    action = session.get(MockAction, action_id)
    if action is None:
        raise LookupError(f"Unknown mock action id: {action_id}")
    return mock_action_to_read(action)


def list_approval_requests(
    session: Session,
    *,
    status: ApprovalStatus | None = None,
    agent_version_id: str | None = None,
    risk_level: RiskLevel | None = None,
    include_decided: bool = False,
) -> list[ApprovalRequestRead]:
    query = (
        select(ApprovalRequest)
        .join(AgentRun, AgentRun.id == ApprovalRequest.run_id)
        .options(
            contains_eager(ApprovalRequest.run),
            selectinload(ApprovalRequest.action).selectinload(
                MockAction.approval_request
            ),
            selectinload(ApprovalRequest.action).selectinload(
                MockAction.audit_events
            ),
        )
    )
    # FR-12: the approval queue lists PENDING approvals by default. An operator
    # opts into history (approved/rejected) with ``include_decided=true``; an
    # explicit ``status`` filter always takes precedence over both defaults.
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
    elif not include_decided:
        query = query.where(ApprovalRequest.status == "pending")
    if agent_version_id is not None:
        query = query.where(AgentRun.agent_version_id == agent_version_id)
    if risk_level is not None:
        query = query.where(ApprovalRequest.risk_level == risk_level)
    query = query.order_by(ApprovalRequest.created_at, ApprovalRequest.id)
    approvals = session.scalars(query).all()
    return [approval_request_to_read(approval) for approval in approvals]


def approve_request(
    session: Session, approval_id: str, payload: ApprovalDecisionCreate
) -> ApprovalRequestRead:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise LookupError(f"Unknown approval request id: {approval_id}")

    _lock_approval_run(session, approval.run_id)

    now = utcnow_naive()
    claim = session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.status == "pending",
        )
        .values(
            status="approved",
            decided_by=APPROVER_ACTOR,
            decision_notes=payload.notes,
            decided_at=now,
        )
    )
    if claim.rowcount != 1:
        session.rollback()
        current = session.get(ApprovalRequest, approval_id)
        current_status = current.status if current is not None else "unknown"
        raise ValueError(
            f"Approval request {approval_id} has already been {current_status}"
        )

    action = approval.action
    action.status = "executed"
    action.executed_at = now
    action.updated_at = now

    _record_audit_event(
        session,
        run_id=approval.run_id,
        action_id=action.id,
        approval_request_id=approval.id,
        event_type="approved",
        actor=APPROVER_ACTOR,
        notes=payload.notes,
        metadata={"risk_level": action.risk_level, "action_type": action.action_type},
        created_at=now,
    )
    _record_audit_event(
        session,
        run_id=approval.run_id,
        action_id=action.id,
        approval_request_id=approval.id,
        event_type="executed",
        actor="mock-action-runner",
        notes="High-risk mock action executed after approval.",
        metadata={"risk_level": action.risk_level, "action_type": action.action_type},
        created_at=now,
    )
    _complete_waiting_run_if_decided(session, approval.run_id, now=now)
    session.commit()
    _invalidate_run_detail_cache(approval.run_id)
    return get_approval_request(session, approval_id)


def reject_request(
    session: Session, approval_id: str, payload: ApprovalDecisionCreate
) -> ApprovalRequestRead:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise LookupError(f"Unknown approval request id: {approval_id}")

    _lock_approval_run(session, approval.run_id)

    now = utcnow_naive()
    claim = session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.status == "pending",
        )
        .values(
            status="rejected",
            decided_by=APPROVER_ACTOR,
            decision_notes=payload.notes,
            decided_at=now,
        )
    )
    if claim.rowcount != 1:
        session.rollback()
        current = session.get(ApprovalRequest, approval_id)
        current_status = current.status if current is not None else "unknown"
        raise ValueError(
            f"Approval request {approval_id} has already been {current_status}"
        )

    action = approval.action
    action.status = "rejected"
    action.updated_at = now

    _record_audit_event(
        session,
        run_id=approval.run_id,
        action_id=action.id,
        approval_request_id=approval.id,
        event_type="rejected",
        actor=APPROVER_ACTOR,
        notes=payload.notes,
        metadata={"risk_level": action.risk_level, "action_type": action.action_type},
        created_at=now,
    )
    _complete_waiting_run_if_decided(session, approval.run_id, now=now)
    session.commit()
    _invalidate_run_detail_cache(approval.run_id)
    return get_approval_request(session, approval_id)


def _invalidate_run_detail_cache(run_id: str) -> None:
    Cache().delete(f"agent:run:{run_id}")


def _lock_approval_run(session: Session, run_id: str) -> AgentRun:
    """Serialize run-scoped approval state changes on the run row.

    Approvals are separate rows, so two operators can decide different pending
    actions concurrently. Locking their shared run prevents both transactions
    from observing the other approval as still pending and leaving the run
    stranded in ``waiting_for_approval``. The operator mock-action creator
    takes the same lock so its run-state check serializes with decisions and
    executor finalization.
    """
    run = session.scalar(
        select(AgentRun).where(AgentRun.id == run_id).with_for_update()
    )
    if run is None:
        raise LookupError(f"Unknown agent run id: {run_id}")
    return run


def _complete_waiting_run_if_decided(
    session: Session, run_id: str, *, now
) -> None:
    pending_count = int(
        session.scalar(
            select(func.count(ApprovalRequest.id)).where(
                ApprovalRequest.run_id == run_id,
                ApprovalRequest.status == "pending",
            )
        )
        or 0
    )
    if pending_count:
        return

    validate_transition("waiting_for_approval", "running")
    resumed = session.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.status == "waiting_for_approval",
        )
        .values(status="running", updated_at=now)
    )
    if resumed.rowcount != 1:
        return
    validate_transition("running", "succeeded")
    completed = session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "running")
        .values(status="succeeded", completed_at=now, updated_at=now)
    )
    if completed.rowcount != 1:
        raise RuntimeError(f"Run {run_id} could not complete after approval resume")


def get_approval_request(session: Session, approval_id: str) -> ApprovalRequestRead:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise LookupError(f"Unknown approval request id: {approval_id}")
    return approval_request_to_read(approval)


def classify_action_risk(action_type: str) -> RiskLevel:
    return "high" if action_type in HIGH_RISK_ACTION_TYPES else "low"


def validate_action_payload(payload: MockActionCreate) -> None:
    contract = PAYLOAD_CONTRACTS[payload.action_type]
    payload_keys = set(payload.payload)
    missing = contract["required"] - payload_keys
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"{payload.action_type} payload is missing required fields: {missing_list}")

    unknown = payload_keys - contract["allowed"]
    if unknown:
        unknown_list = ", ".join(sorted(unknown))
        raise ValueError(f"{payload.action_type} payload includes unsupported fields: {unknown_list}")


def approval_reason(action: MockAction) -> str:
    if action.action_type == "draft_customer_email":
        return "Customer-facing communication requires human approval before mock execution."
    if action.action_type == "update_account_note":
        return "Account-record updates require human approval before mock execution."
    return "High-risk mock action requires human approval before execution."


def customer_email_subject(report: InvestigationReport) -> str:
    if "renewal" in " ".join(report.next_actions).lower():
        return "Follow-up on your renewal"
    return "Status update on your workspace"


def customer_email_body(report: InvestigationReport) -> str:
    return (
        f"We identified this issue for review: {report.root_cause} "
        "The proposed follow-up is a draft and will not be sent from this demo."
    )


def mock_action_to_read(action: MockAction) -> MockActionRead:
    return MockActionRead(
        id=action.id,
        run_id=action.run_id,
        action_type=action.action_type,
        risk_level=action.risk_level,
        status=action.status,
        title=action.title,
        description=action.description,
        target=action.target,
        payload=action.payload,
        created_by=action.created_by,
        created_at=action.created_at,
        updated_at=action.updated_at,
        executed_at=action.executed_at,
        approval_request=approval_summary(action.approval_request)
        if action.approval_request is not None
        else None,
        audit_events=[
            audit_event_to_read(event)
            for event in sorted(action.audit_events, key=lambda item: item.created_at)
        ],
    )


def approval_request_to_read(approval: ApprovalRequest) -> ApprovalRequestRead:
    return ApprovalRequestRead(
        **approval_summary(approval).model_dump(),
        action=mock_action_to_read(approval.action),
    )


def approval_summary(approval: ApprovalRequest) -> ApprovalRequestSummary:
    return ApprovalRequestSummary(
        id=approval.id,
        run_id=approval.run_id,
        agent_version_id=approval.run.agent_version_id,
        action_id=approval.action_id,
        status=approval.status,
        risk_level=approval.risk_level,
        reason=approval.reason,
        requested_by=approval.requested_by,
        decided_by=approval.decided_by,
        decision_notes=approval.decision_notes,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
    )


def audit_event_to_read(event: ActionAuditEvent) -> ActionAuditEventRead:
    return ActionAuditEventRead(
        id=event.id,
        run_id=event.run_id,
        action_id=event.action_id,
        approval_request_id=event.approval_request_id,
        event_type=event.event_type,
        actor=event.actor,
        notes=event.notes,
        metadata=event.event_metadata,
        created_at=event.created_at,
    )


def _record_audit_event(
    session: Session,
    *,
    run_id: str,
    action_id: str,
    approval_request_id: str | None,
    event_type: str,
    actor: str,
    notes: str | None,
    metadata: dict[str, object],
    created_at,
) -> None:
    session.add(
        ActionAuditEvent(
            id=f"aud_{uuid4().hex[:16]}",
            run_id=run_id,
            action_id=action_id,
            approval_request_id=approval_request_id,
            event_type=event_type,
            actor=actor,
            notes=notes,
            event_metadata=metadata,
            created_at=created_at,
        )
    )
