import Link from 'next/link';

import { getDashboardAgents } from '@/lib/api';
import type { AgentVersionObservability } from '@/lib/api';
import { formatCount, formatDateTime, formatPercent, formatUsd } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function ObservabilityDashboardPage() {
  const result = await getDashboardAgents();

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Observability</p>
          <h1>Trace, cost &amp; latency</h1>
        </div>
        <div className="header-actions">
          <Link className="action-button secondary-action" href="/runs">
            Run timeline
          </Link>
        </div>
      </header>

      {!result.ok ? (
        <section className="empty-state">
          <h2>Dashboard unavailable</h2>
          <p className="error-detail">{result.error}</p>
        </section>
      ) : result.data.length === 0 ? (
        <section className="empty-state">
          <h2>No runs yet</h2>
          <p>
            Per-version aggregates appear here once an investigation run is
            recorded. Launch a run from an incident or the run timeline.
          </p>
        </section>
      ) : (
        <VersionAggregateTable versions={result.data} />
      )}

      <p className="footnote">
        Cost values are estimates derived from token usage and never imply
        billing precision (PRD AC-6.4). p95 latency uses the nearest-rank method
        over <code>completed_at - started_at</code> per run (PRD FR-19).
      </p>
    </main>
  );
}

function VersionAggregateTable({
  versions,
}: {
  versions: AgentVersionObservability[];
}) {
  const totalRuns = versions.reduce((sum, v) => sum + v.total_runs, 0);
  const totalCost = versions.reduce((sum, v) => sum + v.total_cost_estimate_usd, 0);
  const totalSucceeded = versions.reduce((sum, v) => sum + v.successful_runs, 0);
  const overallSuccessRate = totalRuns > 0 ? (totalSucceeded / totalRuns) * 100 : 0;

  return (
    <>
      <section className="snapshot-bar">
        <div>
          <span className="label">Versions</span>
          <strong>{formatCount(versions.length)}</strong>
        </div>
        <div>
          <span className="label">Total runs</span>
          <strong>{formatCount(totalRuns)}</strong>
        </div>
        <div>
          <span className="label">Success rate</span>
          <strong>{formatPercent(overallSuccessRate)}</strong>
        </div>
        <div>
          <span className="label">Total estimated cost</span>
          <strong>{formatUsd(totalCost)}</strong>
        </div>
      </section>

      <section className="panel table-panel">
        <div className="panel-header">
          <h2>Per-agent-version aggregates</h2>
          <span>{formatCount(versions.length)} versions</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Agent / version</th>
                <th>Model</th>
                <th>Runs</th>
                <th>Success rate</th>
                <th>Avg latency</th>
                <th>p95 latency</th>
                <th>Avg cost (est.)</th>
                <th>Total cost (est.)</th>
                <th>Last run</th>
                <th aria-label="Drill-down" />
              </tr>
            </thead>
            <tbody>
              {versions.map((entry) => (
                <tr key={entry.agent_version_id}>
                  <td>
                    <div className="cell-stack">
                      <Link href={`/agents/${entry.agent_id}`}>{entry.agent_name}</Link>
                      <Link
                        className="muted"
                        href={`/agents/${entry.agent_id}/versions/${entry.agent_version_id}`}
                      >
                        {entry.semantic_version ? `v${entry.semantic_version}` : '—'}
                      </Link>
                    </div>
                  </td>
                  <td>
                    <code>{entry.model}</code>
                  </td>
                  <td>{formatCount(entry.total_runs)}</td>
                  <td>{formatPercent(entry.success_rate * 100)}</td>
                  <td>{formatLatency(entry.avg_latency_ms)}</td>
                  <td>{formatLatency(entry.p95_latency_ms)}</td>
                  <td>{formatUsd(entry.avg_cost_estimate_usd)}</td>
                  <td>{formatUsd(entry.total_cost_estimate_usd)}</td>
                  <td>
                    {entry.last_run_at ? formatDateTime(entry.last_run_at) : '—'}
                  </td>
                  <td>
                    <Link
                      className="action-button secondary-action"
                      href={`/runs?agent_version_id=${encodeURIComponent(entry.agent_version_id)}`}
                    >
                      Runs
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function formatLatency(value: number | null): string {
  if (value === null) return '—';
  return `${formatCount(Math.round(value))} ms`;
}
