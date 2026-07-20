import Link from 'next/link';

import { TraceLink } from '@/app/TraceLink';
import { listAgentRuns } from '@/lib/api';
import { formatDateTime, formatUsd } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function AgentRunsPage() {
  const result = await listAgentRuns();

  if (!result.ok) {
    return (
      <main className="dashboard-shell">
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            Failed to load agent runs: {result.error}
          </div>
        </section>
      </main>
    );
  }

  const runs = result.data;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Operational workspace</p>
          <h1>Agent Runs</h1>
        </div>
      </header>

      <section className="panel table-panel">
        <div className="panel-header">
          <h2>All runs</h2>
          <span>{runs.length} total</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Incident</th>
                <th>Status</th>
                <th>Started</th>
                <th>Completed</th>
                <th>Tokens</th>
                <th>Cost</th>
                <th>Trace</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <Link href={`/agent/runs/${run.id}`}>{run.id}</Link>
                  </td>
                  <td>{run.incident_id ?? '—'}</td>
                  <td>
                    <span className={`run-status run-status-${run.status}`}>
                      {run.status}
                    </span>
                  </td>
                  <td>{run.started_at ? formatDateTime(run.started_at) : '—'}</td>
                  <td>{run.completed_at ? formatDateTime(run.completed_at) : '—'}</td>
                  <td>{run.token_estimate}</td>
                  <td>{formatUsd(run.cost_estimate_usd)}</td>
                  <td>
                    <TraceLink
                      traceUrl={run.trace_url}
                      traceId={run.trace_id}
                      externalLabel={run.trace_provider ?? 'trace'}
                      fallback={run.trace_provider ?? '—'}
                      externalNewTab
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
