import { listTools, type ToolPermissionScope } from '@/lib/api';

export const dynamic = 'force-dynamic';

const scopeLabels: Record<ToolPermissionScope, string> = {
  read_data: 'Read data',
  write_mock_action: 'Write mock action',
  request_approval: 'Request approval',
  run_eval: 'Run eval',
};

function scopeClass(scope: ToolPermissionScope): string {
  return `tool-scope tool-scope-${scope.replaceAll('_', '-')}`;
}

function formatSchema(schema: Record<string, unknown>): string {
  return JSON.stringify(schema, null, 2);
}

export default async function ToolsPage() {
  const result = await listTools();

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Control plane</p>
          <h1>Tool registry</h1>
          <p className="dashboard-subtitle">
            The governed callable surface, including runtime bindings, permission scopes,
            and source-derived JSON schemas.
          </p>
        </div>
      </header>

      {!result.ok ? (
        <section className="panel anomaly-panel">
          <div className="panel-message error-detail">
            Failed to load tools: {result.error}
          </div>
        </section>
      ) : result.data.tools.length === 0 ? (
        <section className="panel">
          <div className="panel-message">No tools registered.</div>
        </section>
      ) : (
        <section className="panel tool-registry-panel">
          <div className="panel-header">
            <div>
              <h2>Registered tools</h2>
              <span>Permission scope and schemas are read-only on this surface.</span>
            </div>
            <strong>{result.data.total} total</strong>
          </div>

          <div className="tool-registry-list">
            {result.data.tools.map((tool) => (
              <article className="tool-registry-row" key={tool.id}>
                <div className="tool-registry-summary">
                  <div>
                    <div className="tool-title-line">
                      <h3>{tool.name}</h3>
                      <span className={scopeClass(tool.permission_scope)}>
                        {scopeLabels[tool.permission_scope]}
                      </span>
                    </div>
                    <p>{tool.description}</p>
                  </div>
                  <dl className="tool-registry-meta">
                    <div>
                      <dt>Tool ID</dt>
                      <dd><code>{tool.id}</code></dd>
                    </div>
                    <div>
                      <dt>Permission scope</dt>
                      <dd><code>{tool.permission_scope}</code></dd>
                    </div>
                    <div>
                      <dt>implementation_ref</dt>
                      <dd><code>{tool.implementation_ref}</code></dd>
                    </div>
                  </dl>
                </div>

                <div className="tool-schema-grid">
                  <details>
                    <summary>Input schema</summary>
                    <pre>{formatSchema(tool.input_schema)}</pre>
                  </details>
                  <details>
                    <summary>Output schema</summary>
                    <pre>{formatSchema(tool.output_schema)}</pre>
                  </details>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
