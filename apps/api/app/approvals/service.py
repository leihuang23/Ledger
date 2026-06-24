from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

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
from app.models import ActionAuditEvent, AgentRun, ApprovalRequest, MockAction

HIGH_RISK_ACTION_TYPES = {"draft_customer_email", "update_account_note"}
REPORT_ACTION_TYPES = (
    "draft_slack_message",
    "draft_customer_email",
    "create_task",
    "update_account_note",
)
ACTION_ACTOR = "agent"
APPROVER_ACTOR = "demo-approver"
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


def create_mock_action(session: Session, payload: MockActionCreate) -> MockActionRead:
    run = session.get(AgentRun, payload.run_id)
    if run is None:
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
    session: Session, *, run_id: str, report: InvestigationReport
) -> list[MockActionRead]:
    existing_actions = session.scalars(
        select(MockAction).where(MockAction.run_id == run_id)
    ).all()
    existing_action_types = {action.action_type for action in existing_actions}
    if set(REPORT_ACTION_TYPES).issubset(existing_action_types):
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
    session: Session, *, status: ApprovalStatus | None = None
) -> list[ApprovalRequestRead]:
    query = select(ApprovalRequest).order_by(ApprovalRequest.created_at, ApprovalRequest.id)
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
    approvals = session.scalars(query).all()
    return [approval_request_to_read(approval) for approval in approvals]


def approve_request(
    session: Session, approval_id: str, payload: ApprovalDecisionCreate
) -> ApprovalRequestRead:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise LookupError(f"Unknown approval request id: {approval_id}")
    if approval.status != "pending":
        raise ValueError(f"Approval request {approval_id} has already been {approval.status}")

    now = utcnow_naive()
    action = approval.action
    approval.status = "approved"
    approval.decided_by = APPROVER_ACTOR
    approval.decision_notes = payload.notes
    approval.decided_at = now
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
    session.commit()
    return get_approval_request(session, approval_id)


def reject_request(
    session: Session, approval_id: str, payload: ApprovalDecisionCreate
) -> ApprovalRequestRead:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise LookupError(f"Unknown approval request id: {approval_id}")
    if approval.status != "pending":
        raise ValueError(f"Approval request {approval_id} has already been {approval.status}")

    now = utcnow_naive()
    action = approval.action
    approval.status = "rejected"
    approval.decided_by = APPROVER_ACTOR
    approval.decision_notes = payload.notes
    approval.decided_at = now
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
    session.commit()
    return get_approval_request(session, approval_id)


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
