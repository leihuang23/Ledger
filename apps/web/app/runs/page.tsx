import Link from 'next/link';

import { listRuns } from '@/lib/api';
import { formatDateTime, formatUsd } from '@/lib/format';

export const dynamic = 'force-dynamic';

const STATUS_FILTERS = [
  'all',
  'queued',
  'running',
  'waiting_for_approval',
  'succeeded',
  'failed',
] as const;

type RunsPageProps = {
  searchParams?: Promise<{
    status?: string;
    agent_version_id?: string;
  }>;
};

export default async function RunsPage({ searchParams }: RunsPageProps) {
  const resolvedSearchParams = await searchParams;
  const statusParam =
    resolvedSearchParams?.status && resolvedSearchParams.status !== 'all'
      ? resolvedSearchParams.status
      : undefined;
  const agentVersionId = resolvedSearchParams?.agent_version_id;

  const result = await listRuns({
    status: statusParam,
    agent_version_id: agentVersionId,
  });

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Control plane</p>
          <h1>Runs</h1>
        </div>
        {agentVersionId ? (
          <Link
            className="action-button secondary-action"
            href={`/runs${statusParam ? `?status=${encodeURIComponent(statusParam)}` : ''}`}
          >
            Clear version filter ({agentVersionId})
          </Link>
        ) : null}
      </header>

      <section className="panel table-panel">
        <div className="panel-header">
          <h2>All runs</h2>
          {result.ok ? <span>{result.data.length} shown</span> : null}
        </div>
        <div className="filter-row">
          {STATUS_FILTERS.map((filter) => {
            const isActive =
              (filter === 'all' && !statusParam) || filter === statusParam;
            const params = new URLSearchParams();
            if (filter !== 'all') {
              params.set('status', filter);
            }
            if (agentVersionId) {
              params.set('agent_version_id', agentVersionId);
            }
            const query = params.toString();
            return (
              <Link
                key={filter}
                href={`/runs${query ? `?${query}` : ''}`}
                aria-current={isActive ? 'page' : undefined}
                className={isActive ? 'nav-link-active' : undefined}
              >
                {filter === 'all' ? 'All' : filter.replaceAll('_', ' ')}
              </Link>
            );
          })}
        </div>
        {!result.ok ? (
          <div className="panel-message error-detail">
            Failed to load runs: {result.error}
          </div>
        ) : result.data.length === 0 ? (
          <div className="panel-message">No runs match the current filters.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Agent version</th>
                  <th>Status</th>
                  <th>Incident</th>
                  <th>Started</th>
                  <th>Completed</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                  <th>Trace</th>
                </tr>
              </thead>
              <tbody>
                {result.data.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link href={`/runs/${run.id}`}>{run.id}</Link>
                    </td>
                    <td>
                      {run.agent_version_id ? (
                        <code>{run.agent_version_id}</code>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      <span className={`run-status run-status-${run.status}`}>
                        {run.status}
                      </span>
                    </td>
                    <td>
                      {run.incident_id ? (
                        <Link href={`/incidents/${run.incident_id}`}>
                          {run.incident_id}
                        </Link>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>{run.started_at ? formatDateTime(run.started_at) : '—'}</td>
                    <td>
                      {run.completed_at ? formatDateTime(run.completed_at) : '—'}
                    </td>
                    <td>{run.token_estimate}</td>
                    <td>{formatUsd(run.cost_estimate_usd)}</td>
                    <td>
                      {run.trace_url && run.trace_url.startsWith('http') ? (
                        <a href={run.trace_url} target="_blank" rel="noreferrer">
                          {run.trace_provider}
                        </a>
                      ) : (
                        run.trace_provider ?? '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
