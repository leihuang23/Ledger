import Link from 'next/link';
import { notFound } from 'next/navigation';

import { saveAgentVersionDraft } from '@/app/actions';
import { ReadOnlyOperatorNotice } from '@/app/ReadOnlyOperatorNotice';
import { getAgent } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import { operatorMutationsEnabled } from '@/lib/operatorMutations';

export const dynamic = 'force-dynamic';

function versionBadge(status: string) {
  if (status === 'published') return 'action-status action-executed';
  if (status === 'draft') return 'action-status audit-proposed';
  return 'action-status';
}

export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = await params;
  const mutationsEnabled = operatorMutationsEnabled();
  const result = await getAgent(agentId);

  if (!result.ok) {
    if (result.error === 'Agent endpoint returned HTTP 404') {
      notFound();
    }
    return (
      <main className="dashboard-shell">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow">Control plane</p>
            <h1>Agent unavailable</h1>
          </div>
        </header>
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">{result.error}</div>
        </section>
      </main>
    );
  }

  const agent = result.data;
  const publishedVersions = agent.versions.filter((v) => v.status === 'published');
  const draftVersions = agent.versions.filter((v) => v.status === 'draft');

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">
            <Link href="/agents" style={{ color: 'var(--muted)', textDecoration: 'none' }}>
              ← Agents
            </Link>
          </p>
          <h1>{agent.name}</h1>
          <p className="approval-meta" style={{ marginTop: '8px' }}>
            <code>{agent.id}</code> · default model: {agent.default_model} · created{' '}
            {formatDateTime(agent.created_at)}
          </p>
        </div>
        <div className="header-actions">
          <form action={saveAgentVersionDraft}>
            <input type="hidden" name="agent_id" value={agent.id} />
            {agent.latest_published_version ? (
              <input type="hidden" name="base_version_id" value={agent.latest_published_version.id} />
            ) : null}
            <button className="action-button" disabled={!mutationsEnabled} type="submit">
              New draft version
            </button>
          </form>
          {!mutationsEnabled ? (
            <ReadOnlyOperatorNotice className="operator-read-only-note-compact" />
          ) : null}
        </div>
      </header>

      {agent.description ? (
        <section className="snapshot-bar" style={{ marginBottom: '16px' }}>
          <div>
            <span>Description</span>
            <strong style={{ fontSize: '14px', fontWeight: '400' }}>{agent.description}</strong>
          </div>
        </section>
      ) : null}

      <section className="snapshot-bar">
        <div>
          <span>Published versions</span>
          <strong>{publishedVersions.length}</strong>
        </div>
        <div>
          <span>Draft versions</span>
          <strong>{draftVersions.length}</strong>
        </div>
        <div>
          <span>Total versions</span>
          <strong>{agent.version_count}</strong>
        </div>
        <div>
          <span>Last updated</span>
          <strong style={{ fontSize: '14px' }}>{formatDateTime(agent.updated_at)}</strong>
        </div>
      </section>

      <section className="content-grid" style={{ gridTemplateColumns: '1fr' }}>
        <section className="panel">
          <div className="panel-header">
            <h2>Versions</h2>
          </div>
          {agent.versions.length === 0 ? (
            <div className="panel-message">No versions yet.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Model</th>
                    <th>Forked from</th>
                    <th>Created</th>
                    <th>Published</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {agent.versions.map((version) => (
                      <tr key={version.id}>
                        <td>
                          {version.status === 'published' ? (
                            <strong>v{version.version_number}</strong>
                          ) : (
                            <span style={{ color: 'var(--muted)' }}>draft</span>
                          )}
                          {version.semantic_version ? (
                            <div style={{ color: 'var(--muted)', fontSize: '12px', marginTop: '2px' }}>
                              {version.semantic_version}
                            </div>
                          ) : null}
                          <div style={{ color: 'var(--muted)', fontSize: '11px', marginTop: '2px' }}>
                            <code>{version.id}</code>
                          </div>
                        </td>
                        <td>
                          <span className={versionBadge(version.status)}>{version.status}</span>
                        </td>
                        <td>{version.model}</td>
                        <td>
                          {version.forked_from_version_id ? (
                            <code>{version.forked_from_version_id}</code>
                          ) : (
                            <span style={{ color: 'var(--muted)' }}>—</span>
                          )}
                        </td>
                        <td style={{ fontSize: '13px' }}>{formatDateTime(version.created_at)}</td>
                        <td style={{ fontSize: '13px' }}>
                          {version.published_at ? formatDateTime(version.published_at) : '—'}
                        </td>
                        <td>
                          <Link
                            href={`/agents/${agent.id}/versions/${version.id}`}
                            className="action-button secondary-action"
                            style={{ textDecoration: 'none', minHeight: '30px', padding: '6px 10px', fontSize: '12px' }}
                          >
                            Inspect
                          </Link>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
