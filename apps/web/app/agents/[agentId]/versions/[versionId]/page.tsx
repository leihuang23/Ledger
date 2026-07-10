import Link from 'next/link';
import { notFound } from 'next/navigation';

import { launchControlPlaneRun, publishAgentVersion, saveAgentVersionDraft } from '@/app/actions';
import { ReadOnlyOperatorNotice } from '@/app/ReadOnlyOperatorNotice';
import {
  getAgent,
  getAgentVersion,
  listAgentVersions,
  listIncidents,
  listTools,
} from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import { operatorMutationsEnabled } from '@/lib/operatorMutations';

export const dynamic = 'force-dynamic';

const COMMON_MODELS = [
  'gpt-4o-mini',
  'gpt-4o',
  'claude-3-5-sonnet-latest',
  'claude-3-haiku-20240307',
];

function versionBadge(status: string) {
  if (status === 'published') return 'action-status action-executed';
  if (status === 'draft') return 'action-status audit-proposed';
  return 'action-status';
}

export default async function AgentVersionPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentId: string; versionId: string }>;
  searchParams?: Promise<{
    draft_saved?: string;
    publish_error?: string;
    version_error?: string;
    launch_error?: string;
  }>;
}) {
  const { agentId, versionId } = await params;
  const mutationsEnabled = operatorMutationsEnabled();
  const resolvedSearchParams = await searchParams;
  const draftSaved = resolvedSearchParams?.draft_saved === '1';
  const publishError =
    typeof resolvedSearchParams?.publish_error === 'string'
      ? resolvedSearchParams.publish_error
      : null;
  const versionError =
    typeof resolvedSearchParams?.version_error === 'string'
      ? resolvedSearchParams.version_error
      : null;
  const launchError =
    typeof resolvedSearchParams?.launch_error === 'string'
      ? resolvedSearchParams.launch_error
      : null;

  const [agentResult, versionResult, versionsResult, incidentsResult, toolsResult] = await Promise.all([
    getAgent(agentId),
    getAgentVersion(agentId, versionId),
    listAgentVersions(agentId, { limit: 50 }),
    listIncidents({ limit: 25 }),
    listTools(),
  ]);

  if (!agentResult.ok || !versionResult.ok) {
    const errorMessage = !agentResult.ok
      ? agentResult.error
      : !versionResult.ok
        ? versionResult.error
        : 'Failed to load version';
    if (
      errorMessage === 'Agent endpoint returned HTTP 404' ||
      errorMessage === 'Version endpoint returned HTTP 404'
    ) {
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
  const versions = versionsResult.ok ? versionsResult.data.versions : [];
  const drafts = versions.filter((v) => v.status === 'draft');
  const incidents = incidentsResult.ok ? incidentsResult.data.incidents : [];
  const tools = toolsResult.ok ? toolsResult.data.tools : [];
  const toolIds = tools.length > 0 ? tools.map((tool) => tool.id) : version.enabled_tool_ids;
  const toolLabels = Object.fromEntries(tools.map((tool) => [tool.id, tool.name]));
  const permissionScopes = Array.from(
    new Set(
      tools.length > 0
        ? tools.map((tool) => tool.permission_scope)
        : version.allowed_scopes,
    ),
  );

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
        {version.status === 'draft' ? (
          <div className="header-actions">
            <form action={publishAgentVersion}>
              <input type="hidden" name="agent_id" value={agent.id} />
              <input type="hidden" name="version_id" value={version.id} />
              <button className="action-button" disabled={!mutationsEnabled} type="submit">
                Publish version
              </button>
            </form>
            {!mutationsEnabled ? (
              <ReadOnlyOperatorNotice className="operator-read-only-note-compact" />
            ) : null}
          </div>
        ) : (
          <div className="header-actions">
            <form action={saveAgentVersionDraft}>
              <input type="hidden" name="agent_id" value={agent.id} />
              <input type="hidden" name="base_version_id" value={version.id} />
              <button
                className="action-button secondary-action"
                disabled={!mutationsEnabled}
                type="submit"
              >
                New draft from this version
              </button>
            </form>
            <form action={launchControlPlaneRun} aria-label="Launch control-plane run">
              <input type="hidden" name="agent_id" value={agent.id} />
              <input type="hidden" name="agent_version_id" value={version.id} />
              <label className="launch-incident-label" htmlFor="launch_incident_id">
                Incident
              </label>
              <select
                id="launch_incident_id"
                name="incident_id"
                defaultValue="inc_rev_mrr_wow_drop_20260603"
                disabled={!mutationsEnabled}
              >
                {incidents.length === 0 ? (
                  <option value="inc_rev_mrr_wow_drop_20260603">
                    inc_rev_mrr_wow_drop_20260603
                  </option>
                ) : (
                  incidents.map((incident) => (
                    <option key={incident.id} value={incident.id}>
                      {incident.id}
                    </option>
                  ))
                )}
              </select>
              <button className="action-button" disabled={!mutationsEnabled} type="submit">
                Launch run
              </button>
            </form>
            {!mutationsEnabled ? (
              <ReadOnlyOperatorNotice className="operator-read-only-note-compact" />
            ) : null}
          </div>
        )}
      </header>

      {draftSaved ? (
        <section className="panel" aria-live="polite">
          <div className="panel-message">Draft saved successfully.</div>
        </section>
      ) : null}
      {publishError ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">Publish failed: {publishError}</div>
        </section>
      ) : null}
      {versionError ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">Save failed: {versionError}</div>
        </section>
      ) : null}
      {launchError ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">Run launch failed: {launchError}</div>
        </section>
      ) : null}

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

      {version.status === 'draft' ? (
        <form action={saveAgentVersionDraft} className="report-grid">
          {!mutationsEnabled ? <ReadOnlyOperatorNotice className="report-panel-wide" /> : null}
          <input type="hidden" name="agent_id" value={agent.id} />
          <input type="hidden" name="version_id" value={version.id} />
          <input type="hidden" name="return_to" value={`/agents/${agent.id}/versions/${version.id}`} />

          <section className="panel report-panel-wide">
            <div className="panel-header">
              <h2>System prompt</h2>
              <span>Edit the investigation instructions</span>
            </div>
            <div className="report-body">
              <textarea
                name="system_prompt"
                className="config-textarea"
                defaultValue={version.system_prompt}
                rows={18}
                aria-label="System prompt"
                disabled={!mutationsEnabled}
              />
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Model configuration</h2>
            </div>
            <div className="form-stack">
              <label className="field-label">
                <span>Model</span>
                <input
                  type="text"
                  name="model"
                  className="field-input"
                  defaultValue={version.model}
                  list="model-suggestions"
                  disabled={!mutationsEnabled}
                />
                <datalist id="model-suggestions">
                  {COMMON_MODELS.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
              </label>
              <label className="field-label">
                <span>Temperature ({version.temperature})</span>
                <input
                  type="range"
                  name="temperature"
                  min="0"
                  max="2"
                  step="0.1"
                  defaultValue={version.temperature}
                  className="field-range"
                  disabled={!mutationsEnabled}
                />
              </label>
              <label className="field-label">
                <span>Max tokens</span>
                <input
                  type="number"
                  name="max_tokens"
                  className="field-input"
                  defaultValue={version.max_tokens}
                  min="100"
                  max="16384"
                  disabled={!mutationsEnabled}
                />
              </label>
            </div>
          </section>

          <section className="panel report-panel-wide">
            <div className="panel-header">
              <h2>Enabled tools</h2>
              <span>Select which tools this agent version can call during investigation</span>
            </div>
            <input type="hidden" name="enabled_tool_ids_present" value="1" />
            <div className="form-stack">
              {toolIds.map((toolId) => (
                <label key={toolId} className="checkbox-row">
                  <input
                    type="checkbox"
                    name="enabled_tool_ids"
                    value={toolId}
                    defaultChecked={version.enabled_tool_ids.includes(toolId)}
                    disabled={!mutationsEnabled}
                  />
                  <span>
                    <strong>{toolLabels[toolId] ?? toolId}</strong>
                    <code style={{ marginLeft: '8px', color: 'var(--muted)' }}>{toolId}</code>
                  </span>
                </label>
              ))}
            </div>
            <p className="panel-message" style={{ marginTop: '12px' }}>
              Note: unchecked tools will return empty/neutral evidence during investigation.
              The incident and root-cause diagnosis tool is always available.
            </p>
          </section>

          <section className="panel report-panel-wide">
            <div className="panel-header">
              <h2>Allowed scopes</h2>
              <span>A tool also needs its fixed scope before runtime dispatch</span>
            </div>
            <input type="hidden" name="allowed_scopes_present" value="1" />
            <div className="form-stack">
              {permissionScopes.map((scope) => (
                <label key={scope} className="checkbox-row">
                  <input
                    type="checkbox"
                    name="allowed_scopes"
                    value={scope}
                    defaultChecked={version.allowed_scopes.includes(scope)}
                    disabled={!mutationsEnabled}
                  />
                  <code>{scope}</code>
                </label>
              ))}
            </div>
          </section>

          <section className="panel report-panel-wide">
            <div className="panel-header">
              <h2>Fork / version info</h2>
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
                  <span>Current enabled tools</span>
                </div>
                <div>
                  {version.enabled_tool_ids.length === 0 ? (
                    <span style={{ color: 'var(--muted)' }}>none</span>
                  ) : (
                    version.enabled_tool_ids.join(', ')
                  )}
                </div>
              </div>
            </div>
            <div style={{ marginTop: '16px', display: 'flex', gap: '8px' }}>
              <button className="action-button" disabled={!mutationsEnabled} type="submit">
                Save draft
              </button>
            </div>
          </section>
        </form>
      ) : (
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
                          {toolLabels[toolId] ?? toolId}{' '}
                          <code style={{ color: 'var(--muted)' }}>{toolId}</code>
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

          <section className="panel report-panel-wide">
            <div className="panel-header">
              <h2>Immutability notice</h2>
            </div>
            <div className="panel-message">
              Published versions are frozen and cannot be modified. Use the &quot;New draft from
              this version&quot; button above to iterate on the prompt or configuration, then
              publish it as the next version.
            </div>
            {drafts.length > 0 ? (
              <div style={{ marginTop: '16px' }}>
                <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>Open drafts for this agent</h3>
                <ul style={{ margin: 0, paddingLeft: '20px' }}>
                  {drafts.map((d) => (
                    <li key={d.id}>
                      <Link href={`/agents/${agent.id}/versions/${d.id}`}>
                        Draft <code>{d.id.slice(0, 8)}</code>
                      </Link>{' '}
                      · model: {d.model}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
        </section>
      )}
    </main>
  );
}
