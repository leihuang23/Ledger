import Link from 'next/link';
import { notFound } from 'next/navigation';

import { getAgent, getAgentVersion } from '@/lib/api';
import { formatDateTime } from '@/lib/format';

export const dynamic = 'force-dynamic';

function versionBadge(status: string) {
  if (status === 'published') return 'action-status action-executed';
  if (status === 'draft') return 'action-status audit-proposed';
  return 'action-status';
}

export default async function AgentVersionPage({
  params,
}: {
  params: Promise<{ agentId: string; versionId: string }>;
}) {
  const { agentId, versionId } = await params;

  const [agentResult, versionResult] = await Promise.all([
    getAgent(agentId),
    getAgentVersion(agentId, versionId),
  ]);

  if (!agentResult.ok || !versionResult.ok) {
    const errorMessage = !agentResult.ok ? agentResult.error : !versionResult.ok ? versionResult.error : 'Failed to load version';
    if (errorMessage.includes('HTTP 404')) {
      notFound();
    }
    return (
      <main className="dashboard-shell">
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            {errorMessage}
          </div>
        </section>
      </main>
    );
  }

  const agent = agentResult.data;
  const version = versionResult.data;

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">
            <Link href="/agents" style={{ color: 'var(--muted)', textDecoration: 'none' }}>
              ← Agents
            </Link>
            {' / '}
            <Link
              href={`/agents/${agent.id}`}
              style={{ color: 'var(--muted)', textDecoration: 'none' }}
            >
              {agent.name}
            </Link>
          </p>
          <h1>
            {version.status === 'published' ? (
              <>
                v{version.version_number}{' '}
                <span style={{ color: 'var(--muted)', fontWeight: '400', fontSize: '20px' }}>
                  ({version.semantic_version})
                </span>
              </>
            ) : (
              'Draft version'
            )}
          </h1>
          <p className="approval-meta" style={{ marginTop: '8px' }}>
            <span className={versionBadge(version.status)}>{version.status}</span>{' '}
            <code>{version.id}</code> · model: {version.model} · temp: {version.temperature} ·
            max_tokens: {version.max_tokens}
          </p>
        </div>
      </header>

      <section className="snapshot-bar">
        <div>
          <span>Status</span>
          <strong style={{ textTransform: 'capitalize' }}>{version.status}</strong>
        </div>
        <div>
          <span>Model</span>
          <strong style={{ fontSize: '14px' }}>{version.model}</strong>
        </div>
        <div>
          <span>Temperature</span>
          <strong>{version.temperature}</strong>
        </div>
        <div>
          <span>Max tokens</span>
          <strong>{version.max_tokens}</strong>
        </div>
        <div>
          <span>Created</span>
          <strong style={{ fontSize: '14px' }}>{formatDateTime(version.created_at)}</strong>
        </div>
        <div>
          <span>Published</span>
          <strong style={{ fontSize: '14px' }}>
            {version.published_at ? formatDateTime(version.published_at) : '—'}
          </strong>
        </div>
      </section>

      <section className="report-grid">
        <section className="panel">
          <div className="panel-header">
            <h2>System prompt</h2>
          </div>
          <div className="report-body">
            <pre
              style={{
                margin: 0,
                padding: '16px',
                border: '1px solid var(--border)',
                borderRadius: '8px',
                background: '#f8fafc',
                color: 'var(--foreground)',
                fontSize: '13px',
                lineHeight: '1.6',
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                maxHeight: '600px',
                overflow: 'auto',
              }}
            >
              {version.system_prompt || '(empty)'}
            </pre>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Configuration</h2>
          </div>
          <div className="category-stack">
            <div className="category-row">
              <div className="category-label">
                <span>Forked from</span>
              </div>
              <div>
                {version.forked_from_version_id ? (
                  <code>{version.forked_from_version_id}</code>
                ) : (
                  <span style={{ color: 'var(--muted)' }}>scratch</span>
                )}
              </div>
            </div>
            <div className="category-row">
              <div className="category-label">
                <span>Published by</span>
              </div>
              <div>
                {version.published_by ? (
                  <code>{version.published_by}</code>
                ) : (
                  <span style={{ color: 'var(--muted)' }}>—</span>
                )}
              </div>
            </div>
            <div className="category-row">
              <div className="category-label">
                <span>Enabled tools</span>
              </div>
              <div>
                {version.enabled_tool_ids.length === 0 ? (
                  <span style={{ color: 'var(--muted)' }}>none configured</span>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: '20px' }}>
                    {version.enabled_tool_ids.map((toolId) => (
                      <li key={toolId}>
                        <code>{toolId}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <div className="category-row">
              <div className="category-label">
                <span>Allowed scopes</span>
              </div>
              <div>
                {version.allowed_scopes.length === 0 ? (
                  <span style={{ color: 'var(--muted)' }}>none configured</span>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: '20px' }}>
                    {version.allowed_scopes.map((scope) => (
                      <li key={scope}>
                        <code>{scope}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        </section>
      </section>

      {version.status === 'published' ? (
        <section className="panel" style={{ marginTop: '16px' }}>
          <div className="panel-header">
            <h2>Immutability notice</h2>
          </div>
          <div className="panel-message">
            Published versions are frozen and cannot be modified. Create a new draft from this
            version to iterate on the prompt or configuration, then publish it as the next version.
          </div>
        </section>
      ) : null}
    </main>
  );
}
