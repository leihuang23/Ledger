import Link from 'next/link';

import type { AgentRunDetail, AgentRunStep, ReportEvidence } from '@/lib/api';
import { getAgentRun } from '@/lib/api';
import { formatCount, formatDateTime, formatMoney } from '@/lib/format';

type AgentRunPageProps = {
  params: Promise<{
    runId: string;
  }>;
};

export default async function AgentRunPage({ params }: AgentRunPageProps) {
  const { runId } = await params;
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

  return <RunReport run={result.data} />;
}

function RunReport({ run }: { run: AgentRunDetail }) {
  const report = run.final_report;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Investigation {run.id}</p>
          <h1>
            {report?.root_cause ??
              (run.status === 'running'
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
          <strong>{run.trace_id ?? 'not recorded'}</strong>
        </div>
        <div>
          <span className="label">Estimated tokens</span>
          <strong>{formatCount(run.token_estimate)}</strong>
        </div>
      </section>

      {run.error ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">{run.error}</div>
        </section>
      ) : null}

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
                      <td>{account.account_name}</td>
                      <td>{account.segment}</td>
                      <td>{formatMoney(account.failed_invoice_cents)}</td>
                      <td>{account.failed_invoice_ids.join(', ')}</td>
                      <td>{account.ticket_ids.join(', ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <EvidencePanel evidence={report.cited_evidence} />
        </section>
      ) : null}

      <StepHistory steps={run.steps} />
    </main>
  );
}

function EvidencePanel({ evidence }: { evidence: ReportEvidence[] }) {
  return (
    <div className="panel report-panel-wide">
      <div className="panel-header">
        <h2>Cited evidence</h2>
        <span>{formatCount(evidence.length)} citations</span>
      </div>
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
