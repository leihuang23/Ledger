import Link from 'next/link';

import { startInvestigationFromIncident } from '@/app/actions';
import { getIncident } from '@/lib/api';
import {
  formatCount,
  formatDate,
  formatDateTime,
  formatMoney,
  formatPercent,
  formatScenario,
} from '@/lib/format';

type IncidentPageProps = {
  params: Promise<{
    incidentId: string;
  }>;
  searchParams?: Promise<{
    investigation_error?: string;
  }>;
};

export default async function IncidentPage({ params, searchParams }: IncidentPageProps) {
  const { incidentId } = await params;
  const resolvedSearchParams = await searchParams;
  const result = await getIncident(incidentId);
  const investigationError =
    typeof resolvedSearchParams?.investigation_error === 'string'
      ? resolvedSearchParams.investigation_error
      : null;

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow">Incident</p>
            <h1>Incident unavailable</h1>
          </div>
          <Link className="action-button secondary-action" href="/">
            Back to dashboard
          </Link>
        </header>
        <section className="empty-state">
          <h2>Incident data unavailable</h2>
          <p className="error-detail">{result.error}</p>
        </section>
      </main>
    );
  }

  const incident = result.data;
  const metric = incident.metric_evidence;
  const sourceQueries = incident.evidence.source_queries ?? [];

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Incident {incident.id}</p>
          <h1>{incident.title}</h1>
        </div>
        <div className="header-actions">
          <span className={`severity-pill severity-${incident.severity}`}>
            {incident.severity}
          </span>
          <span className="status-pill incident-status">{incident.status}</span>
          <form action={startInvestigationFromIncident}>
            <input name="incident_id" type="hidden" value={incident.id} />
            <button className="action-button" type="submit">
              Run investigation
            </button>
          </form>
          <Link className="action-button secondary-action" href="/knowledge">
            Knowledge
          </Link>
          <Link className="action-button secondary-action" href="/">
            Dashboard
          </Link>
        </div>
      </header>

      {investigationError ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">{investigationError}</div>
        </section>
      ) : null}

      <section className="snapshot-bar">
        <div>
          <span className="label">Detected at</span>
          <strong>{formatDateTime(incident.detected_at)}</strong>
        </div>
        <div>
          <span className="label">Paid MRR delta</span>
          <strong>{formatMoney(metric.delta_cents)}</strong>
        </div>
        <div>
          <span className="label">Drop percent</span>
          <strong>{formatPercent(metric.delta_percent)}</strong>
        </div>
        <div>
          <span className="label">Affected accounts</span>
          <strong>{formatCount(incident.affected_accounts.length)}</strong>
        </div>
      </section>

      <section className="content-grid incident-grid">
        <div className="panel table-panel">
          <div className="panel-header">
            <h2>Metric evidence</h2>
            <span>
              {formatDate(metric.current_window_start)} -{' '}
              {formatDate(metric.current_window_end)}
            </span>
          </div>
          <dl className="evidence-list">
            <div>
              <dt>Current paid invoice MRR</dt>
              <dd>{formatMoney(metric.current_value_cents)}</dd>
            </div>
            <div>
              <dt>Previous paid invoice MRR</dt>
              <dd>{formatMoney(metric.previous_value_cents)}</dd>
            </div>
            <div>
              <dt>Failed current renewals</dt>
              <dd>
                {formatCount(metric.failed_invoice_count)} invoices worth{' '}
                {formatMoney(metric.failed_invoice_cents)}
              </dd>
            </div>
            <div>
              <dt>Invoice evidence</dt>
              <dd>{metric.invoice_ids.join(', ')}</dd>
            </div>
          </dl>
        </div>

        <div className="panel table-panel">
          <div className="panel-header">
            <h2>Affected accounts</h2>
            <span>{formatScenario(incident.source_scenario)}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Segment</th>
                  <th>Failed amount</th>
                  <th>Health</th>
                </tr>
              </thead>
              <tbody>
                {incident.affected_accounts.map((account) => (
                  <tr key={account.account_id}>
                    <td>{account.account_name}</td>
                    <td>{account.segment}</td>
                    <td>{formatMoney(account.failed_invoice_cents)}</td>
                    <td>{account.health_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>Support signals</h2>
            <span>{formatCount(incident.support_signals.length)} tickets</span>
          </div>
          <div className="signal-stack">
            {incident.support_signals.slice(0, 6).map((ticket) => (
              <div className="signal-row" key={ticket.ticket_id}>
                <strong>{ticket.subject}</strong>
                <span>
                  {ticket.account_name} / {ticket.priority} / {ticket.status}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>Product signals</h2>
            <span>{formatCount(incident.product_signals.length)} event groups</span>
          </div>
          <div className="signal-stack">
            {incident.product_signals.map((signal) => (
              <div className="signal-row" key={`${signal.event_name}-${signal.source_scenario}`}>
                <strong>{formatScenario(signal.event_name)}</strong>
                <span>
                  {formatCount(signal.event_count)} events across{' '}
                  {formatCount(signal.affected_accounts)} accounts
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>Evidence sources</h2>
            <span>{formatCount(sourceQueries.length)} queries</span>
          </div>
          <ul className="source-list">
            {sourceQueries.map((query) => (
              <li key={query}>{query}</li>
            ))}
          </ul>
        </div>
      </section>
    </main>
  );
}
