import Link from 'next/link';

import {
  approveApprovalFromRun,
  rejectApprovalFromRun,
} from '@/app/actions';
import { RunRefresh } from './RunRefresh';
import type {
  ActionAuditEvent,
  AgentRunDetail,
  AgentRunStep,
  MockAction,
  ReportClaim,
  ReportEvidence,
} from '@/lib/api';
import { getAgentRun } from '@/lib/api';
import { formatCount, formatDateTime, formatMoney, formatUsd } from '@/lib/format';

type AgentRunPageProps = {
  params: Promise<{
    runId: string;
  }>;
  searchParams: Promise<{
    approval_error?: string;
  }>;
};

export default async function AgentRunPage({ params, searchParams }: AgentRunPageProps) {
  const { runId } = await params;
  const { approval_error: approvalError } = await searchParams;
  const result = await getAgentRun(runId);

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow">Investigation</p>
            <h1>Run unavailable</h1>
          </div>
          <Link className="action-button secondary-action" href="/">
            Dashboard
          </Link>
        </header>
        <section className="empty-state">
          <h2>Agent run unavailable</h2>
          <p className="error-detail">{result.error}</p>
        </section>
      </main>
    );
  }

  return <RunReport approvalError={approvalError} run={result.data} />;
}

function RunReport({
  approvalError,
  run,
}: {
  approvalError?: string;
  run: AgentRunDetail;
}) {
  const report = run.final_report;
  const runIsActive = !run.is_stale && (run.status === 'queued' || run.status === 'running');

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Investigation {run.id}</p>
          <h1>
            {report?.root_cause ??
              (run.is_stale
                ? 'Investigation interrupted before completion'
                : runIsActive
                  ? 'Investigation in progress'
                  : 'Investigation failed before report synthesis')}
          </h1>
        </div>
        <div className="header-actions">
          <span className={`run-status run-status-${run.status}`}>{run.status}</span>
          <Link className="action-button secondary-action" href={`/incidents/${run.incident_id}`}>
            Incident
          </Link>
          <Link className="action-button secondary-action" href="/">
            Dashboard
          </Link>
        </div>
      </header>

      <section className="snapshot-bar">
        <div>
          <span className="label">Started</span>
          <strong>{run.started_at ? formatDateTime(run.started_at) : 'Not started'}</strong>
        </div>
        <div>
          <span className="label">Completed</span>
          <strong>{run.completed_at ? formatDateTime(run.completed_at) : 'In progress'}</strong>
        </div>
        <div>
          <span className="label">Trace</span>
          <strong>
            {run.trace_url && run.trace_url.startsWith('http') ? (
              <a href={run.trace_url}>{run.trace_id ?? run.trace_provider ?? 'trace'}</a>
            ) : (
              run.trace_url ?? run.trace_id ?? 'not recorded'
            )}
          </strong>
        </div>
        <div>
          <span className="label">Estimated tokens</span>
          <strong>{formatCount(run.token_estimate)}</strong>
          {run.prompt_tokens > 0 || run.completion_tokens > 0 ? (
            <span className="token-breakdown">
              {' '}
              ({formatCount(run.prompt_tokens)} prompt / {formatCount(run.completion_tokens)} completion)
            </span>
          ) : null}
        </div>
        <div>
          <span className="label">Estimated cost</span>
          <strong>{formatUsd(run.cost_estimate_usd)}</strong>
        </div>
      </section>

      {run.error ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">{run.error}</div>
        </section>
      ) : null}
      {run.is_stale ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">
            No recent execution activity was recorded for this run.
          </div>
        </section>
      ) : null}

      <RunRefresh active={runIsActive} />

      {report ? (
        <section className="report-grid">
          <div className="panel report-panel-wide">
            <div className="panel-header">
              <h2>Root cause</h2>
              <span className={`confidence-pill confidence-${report.confidence}`}>
                {report.confidence} confidence
              </span>
            </div>
            <div className="report-body">
              <p>{report.summary}</p>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <h2>Next actions</h2>
              <span>{formatCount(report.next_actions.length)} recommended</span>
            </div>
            <ol className="next-action-list">
              {report.next_actions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ol>
          </div>

          <ClaimPanel claims={report.claims ?? []} />

          <div className="panel table-panel report-panel-wide">
            <div className="panel-header">
              <h2>Affected accounts</h2>
              <span>{formatCount(report.affected_accounts.length)} accounts</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Segment</th>
                    <th>Failed amount</th>
                    <th>Invoices</th>
                    <th>Tickets</th>
                  </tr>
                </thead>
                <tbody>
                  {report.affected_accounts.map((account) => (
                    <tr key={account.account_id}>
                      <td>
                        <Link href={`/accounts/${account.account_id}`}>
                          {account.account_name}
                        </Link>
                      </td>
                      <td>{account.segment}</td>
                      <td>{formatMoney(account.failed_invoice_cents)}</td>
                      <td>{account.failed_invoice_ids.join(', ')}</td>
                      <td>
                        {account.ticket_ids.map((ticketId, index) => (
                          <span key={ticketId}>
                            {index > 0 ? ', ' : ''}
                            <Link href={`/support/tickets/${ticketId}`}>{ticketId}</Link>
                          </span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <EvidencePanel evidence={report.cited_evidence} />
        </section>
      ) : null}

      <ApprovalQueuePanel
        actions={run.mock_actions}
        approvalError={approvalError}
        runId={run.id}
      />

      <StepHistory steps={run.steps} />
    </main>
  );
}

function ClaimPanel({ claims }: { claims: ReportClaim[] }) {
  return (
    <div className="panel report-panel-wide">
      <div className="panel-header">
        <h2>Claim citations</h2>
        <span>{formatCount(claims.length)} claims</span>
      </div>
      {claims.length > 0 ? (
        <div className="evidence-stack">
          {claims.map((claim) => (
            <article className="evidence-item" key={`${claim.category}-${claim.text}`}>
              <div>
                <span className="evidence-kind evidence-document">
                  {claim.category.replaceAll('_', ' ')}
                </span>
                <p>{claim.text}</p>
              </div>
              <dl className="citation-grid compact-citation">
                <div>
                  <dt>References</dt>
                  <dd>{claim.citation_refs.join(', ') || 'none'}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel-message">No claim-level citations recorded.</div>
      )}
    </div>
  );
}

function ApprovalQueuePanel({
  actions,
  approvalError,
  runId,
}: {
  actions: MockAction[];
  approvalError?: string;
  runId: string;
}) {
  const pendingCount = actions.filter((action) => action.status === 'pending_approval').length;

  return (
    <section className="panel approval-panel">
      <div className="panel-header">
        <h2>Approval queue</h2>
        <span>
          {formatCount(pendingCount)} pending / {formatCount(actions.length)} actions
        </span>
      </div>
      {approvalError ? (
        <div className="panel-message error-detail" role="alert">
          {approvalError}
        </div>
      ) : null}
      {actions.length > 0 ? (
        <div className="approval-stack">
          {actions.map((action) => (
            <article className="approval-row" key={action.id}>
              <div className="approval-row-main">
                <div>
                  <span className={`risk-pill risk-${action.risk_level}`}>
                    {action.risk_level} risk
                  </span>
                  <h3>{action.title}</h3>
                  <p>{action.description}</p>
                </div>
                <div className="approval-actions">
                  <span className={`action-status action-${action.status}`}>
                    {statusLabel(action.status)}
                  </span>
                  {action.status === 'pending_approval' && action.approval_request ? (
                    <div className="approval-buttons">
                      <form action={approveApprovalFromRun}>
                        <input name="approval_id" type="hidden" value={action.approval_request.id} />
                        <input name="run_id" type="hidden" value={runId} />
                        <button className="action-button" type="submit">
                          Approve
                        </button>
                      </form>
                      <form action={rejectApprovalFromRun}>
                        <input name="approval_id" type="hidden" value={action.approval_request.id} />
                        <input name="run_id" type="hidden" value={runId} />
                        <button className="action-button secondary-action" type="submit">
                          Reject
                        </button>
                      </form>
                    </div>
                  ) : null}
                </div>
              </div>

              <dl className="action-meta">
                <div>
                  <dt>Type</dt>
                  <dd>{actionTypeLabel(action.action_type)}</dd>
                </div>
                <div>
                  <dt>Target</dt>
                  <dd>{action.target}</dd>
                </div>
                <div>
                  <dt>Created</dt>
                  <dd>{formatDateTime(action.created_at)}</dd>
                </div>
                <div>
                  <dt>Executed</dt>
                  <dd>{action.executed_at ? formatDateTime(action.executed_at) : 'not executed'}</dd>
                </div>
              </dl>

              {action.approval_request ? (
                <p className="approval-reason">{action.approval_request.reason}</p>
              ) : null}

              <details>
                <summary>Audit and payload</summary>
                <div className="audit-stack">
                  {action.audit_events.map((event) => (
                    <AuditEventRow event={event} key={event.id} />
                  ))}
                </div>
                <pre>{formatPayload(action.payload)}</pre>
              </details>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel-message">No mock actions proposed for this run.</div>
      )}
    </section>
  );
}

function AuditEventRow({ event }: { event: ActionAuditEvent }) {
  return (
    <div className="audit-event">
      <span className={`audit-event-type audit-${event.event_type}`}>{event.event_type}</span>
      <div>
        <strong>{event.actor}</strong>
        <span>{formatDateTime(event.created_at)}</span>
        {event.notes ? <p>{event.notes}</p> : null}
      </div>
    </div>
  );
}

function EvidencePanel({ evidence }: { evidence: ReportEvidence[] }) {
  return (
    <div className="panel report-panel-wide">
      <div className="panel-header">
        <h2>Cited evidence</h2>
        <span>{formatCount(evidence.length)} citations</span>
      </div>
      {evidence.length > 0 ? (
        <div className="evidence-stack">
          {evidence.map((item) => (
            <article className="evidence-item" key={`${item.kind}-${item.reference_id}`}>
              <div>
                <span className={`evidence-kind evidence-${item.kind}`}>{item.kind}</span>
                <h3>{item.title}</h3>
                <p>{item.summary}</p>
              </div>
              {item.source_query ? <pre>{item.source_query}</pre> : null}
              <dl className="citation-grid compact-citation">
                <div>
                  <dt>Reference</dt>
                  <dd>{item.reference_id}</dd>
                </div>
                <div>
                  <dt>Citation</dt>
                  <dd>{citationLabel(item)}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel-message">No cited evidence recorded for this run.</div>
      )}
    </div>
  );
}

function StepHistory({ steps }: { steps: AgentRunStep[] }) {
  return (
    <section className="panel run-history-panel">
      <div className="panel-header">
        <h2>Tool-step history</h2>
        <span>{formatCount(steps.length)} steps</span>
      </div>
      <div className="timeline">
        {steps.map((step) => (
          <article className="step-row" key={step.id}>
            <div className="step-row-header">
              <div>
                <span className={`step-status step-${step.status}`}>{step.status}</span>
                <h3>{step.tool_name ?? step.stage}</h3>
              </div>
              <span>{formatDateTime(step.started_at)}</span>
            </div>
            <dl className="step-meta">
              <div>
                <dt>Stage</dt>
                <dd>{step.stage}</dd>
              </div>
              <div>
                <dt>Tool</dt>
                <dd>{step.tool_name ?? 'workflow node'}</dd>
              </div>
              <div>
                <dt>Completed</dt>
                <dd>{step.completed_at ? formatDateTime(step.completed_at) : 'pending'}</dd>
              </div>
            </dl>
            {step.error ? <p className="error-detail">{step.error}</p> : null}
            <details>
              <summary>Inputs and outputs</summary>
              <pre>{formatPayload({ inputs: step.inputs, outputs: step.outputs })}</pre>
            </details>
          </article>
        ))}
      </div>
    </section>
  );
}

function citationLabel(item: ReportEvidence) {
  const sourceId = item.citation.source_id;
  const chunkId = item.citation.chunk_id;
  const ticketId = item.citation.ticket_id;

  if (typeof sourceId === 'string' && typeof chunkId === 'string') {
    return `${sourceId} / ${chunkId}`;
  }
  if (typeof ticketId === 'string') {
    return ticketId;
  }
  return 'structured run evidence';
}

function formatPayload(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function actionTypeLabel(actionType: MockAction['action_type']) {
  return actionType.replaceAll('_', ' ');
}

function statusLabel(status: MockAction['status']) {
  return status.replaceAll('_', ' ');
}
