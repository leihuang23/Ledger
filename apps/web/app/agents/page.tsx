import Link from 'next/link';

import { listAgents } from '@/lib/api';
import { formatDateTime } from '@/lib/format';

export const dynamic = 'force-dynamic';

function versionBadge(status: string) {
  if (status === 'published') return 'action-status action-executed';
  if (status === 'draft') return 'action-status audit-proposed';
  return 'action-status';
}

export default async function AgentsPage() {
  const result = await listAgents();

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Control plane</p>
          <h1>Agents</h1>
          <p className="dashboard-subtitle">
            Registered agents, their published versions, and draft configurations.
          </p>
        </div>
      </header>

      {!result.ok ? (
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">Failed to load agents: {result.error}</div>
        </section>
      ) : result.data.agents.length === 0 ? (
        <section className="panel">
          <div className="panel-message">No agents registered.</div>
        </section>
      ) : (
        <section className="panel">
          <div className="panel-header">
            <h2>Registered agents</h2>
            <span>{result.data.total} total</span>
          </div>
          <div className="approval-stack">
            {result.data.agents.map((agent) => (
              <article key={agent.id} className="approval-row">
                <div className="approval-row-main">
                  <div>
                    <h3>
                      <Link href={`/agents/${agent.id}`} style={{ color: 'var(--blue)', textDecoration: 'none' }}>
                        {agent.name}
                      </Link>
                    </h3>
                    <p className="approval-meta">
                      <code>{agent.id}</code> · model: {agent.default_model} · {agent.version_count}{' '}
                      version{agent.version_count === 1 ? '' : 's'} · updated{' '}
                      {formatDateTime(agent.updated_at)}
                    </p>
                    {agent.description ? <p>{agent.description}</p> : null}
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                      {agent.latest_published_version ? (
                        <span className={versionBadge(agent.latest_published_version.status)}>
                          v{agent.latest_published_version.version_number} (
                          {agent.latest_published_version.semantic_version}) ·{' '}
                          {agent.latest_published_version.model} · published{' '}
                          {agent.latest_published_version.published_at
                            ? formatDateTime(agent.latest_published_version.published_at)
                            : '—'}
                        </span>
                      ) : (
                        <span className="action-status" style={{ opacity: 0.7 }}>
                          no published version
                        </span>
                      )}
                      {agent.current_draft_version ? (
                        <span className={versionBadge(agent.current_draft_version.status)}>
                          draft · {agent.current_draft_version.model}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div className="approval-buttons">
                    <Link
                      href={`/agents/${agent.id}`}
                      className="action-button secondary-action"
                      style={{ textDecoration: 'none' }}
                    >
                      View
                    </Link>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
