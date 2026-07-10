import Link from 'next/link';

import { approveApprovalFromQueue, rejectApprovalFromQueue } from '@/app/actions';
import { ReadOnlyOperatorNotice } from '@/app/ReadOnlyOperatorNotice';
import type {
  AgentSummary,
  AgentVersionSummary,
  ApprovalStatus,
  RiskLevel,
} from '@/lib/api';
import { listAgents, listAgentVersions, listApprovalRequests } from '@/lib/api';
import { formatDateTime, formatScenario } from '@/lib/format';
import { operatorMutationsEnabled } from '@/lib/operatorMutations';

export const dynamic = 'force-dynamic';

type ApprovalSearchParams = {
  status?: string;
  agent_version_id?: string;
  risk_level?: string;
  approval_error?: string;
  include_decided?: string;
};

type PublishedVersion = {
  agent: AgentSummary;
  version: AgentVersionSummary;
};

export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<ApprovalSearchParams>;
}) {
  const params = await searchParams;
  const mutationsEnabled = operatorMutationsEnabled();
  const status = readStatus(params.status);
  const riskLevel = readRiskLevel(params.risk_level);
  // FR-12: the queue defaults to pending. The filter form carries
  // ``include_decided=true`` so an operator viewing history (approved/rejected)
  // keeps that context; "Clear filters" omits it and returns to the pending queue.
  const includeDecided = params.include_decided === 'true';
  const [result, versions] = await Promise.all([
    listApprovalRequests({
      status,
      agent_version_id: params.agent_version_id || undefined,
      risk_level: riskLevel,
      include_decided: includeDecided,
    }),
    loadPublishedVersions(),
  ]);

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Platform safety control</p>
          <h1>Approvals</h1>
          <p className="page-summary">
            Review risky mock actions across every run and agent version before execution.
          </p>
        </div>
      </header>

      {params.approval_error ? (
        <section className="panel anomaly-panel" aria-live="polite">
          <div className="panel-message error-detail">{params.approval_error}</div>
        </section>
      ) : null}

      <section className="panel approval-filter-panel">
        <div className="panel-header">
          <h2>Queue filters</h2>
          <Link href="/approvals">Clear filters</Link>
        </div>
        <form action="/approvals" className="approval-filter-form" method="get">
          {/* Carry include_decided so "All statuses" history view survives a
              filter re-apply; "Clear filters" omits it to return to pending. */}
          <input type="hidden" name="include_decided" value="true" />
          <label className="field-label">
            <span>Status</span>
            <select className="field-select" defaultValue={status ?? ''} name="status">
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
            </select>
          </label>
          <label className="field-label">
            <span>Agent version</span>
            <select
              className="field-select"
              defaultValue={params.agent_version_id ?? ''}
              name="agent_version_id"
            >
              <option value="">All agent versions</option>
              {versions.map((item) => (
                <option key={item.version.id} value={item.version.id}>
                  {versionLabel(item)}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            <span>Risk level</span>
            <select className="field-select" defaultValue={riskLevel ?? ''} name="risk_level">
              <option value="">All risk levels</option>
              <option value="high">High</option>
              <option value="low">Low</option>
            </select>
          </label>
          <button className="action-button" type="submit">
            Apply filters
          </button>
        </form>
      </section>

      <section className="panel approval-panel">
        <div className="panel-header">
          <h2>Approval queue</h2>
          <span>{result.ok ? result.data.length : 0} matching</span>
        </div>

        {!mutationsEnabled && result.ok && result.data.some((item) => item.status === 'pending') ? (
          <ReadOnlyOperatorNotice />
        ) : null}

        {!result.ok ? (
          <div className="panel-message error-detail">Failed to load approvals: {result.error}</div>
        ) : result.data.length === 0 ? (
          <div className="panel-message">No approvals match the selected filters.</div>
        ) : (
          <div className="approval-stack">
            {result.data.map((approval) => {
              const version = versions.find(
                ({ version: candidate }) => candidate.id === approval.agent_version_id,
              );
              return (
                <article key={approval.id} className="approval-row">
                  <div className="approval-row-main">
                    <div>
                      <div className="approval-badges">
                        <span className={`action-status action-${approval.status}`}>
                          {approval.status}
                        </span>
                        <span className={`risk-pill risk-${approval.risk_level}`}>
                          {approval.risk_level} risk
                        </span>
                      </div>
                      <h3>{approval.action.title}</h3>
                      <p>{approval.action.description}</p>
                      <dl className="approval-meta-grid">
                        <div>
                          <dt>Agent version</dt>
                          <dd>{version ? versionLabel(version) : approval.agent_version_id ?? 'legacy run'}</dd>
                        </div>
                        <div>
                          <dt>Run</dt>
                          <dd>{approval.run_id}</dd>
                        </div>
                        <div>
                          <dt>Requested</dt>
                          <dd>{formatDateTime(approval.created_at)}</dd>
                        </div>
                        <div>
                          <dt>Requested by</dt>
                          <dd>{approval.requested_by}</dd>
                        </div>
                      </dl>
                    </div>
                    {approval.status === 'pending' ? (
                      <div className="approval-buttons">
                        <form action={approveApprovalFromQueue}>
                          <ApprovalDecisionFields approvalId={approval.id} params={params} />
                          <button
                            type="submit"
                            className="action-button"
                            disabled={!mutationsEnabled}
                          >
                            Approve
                          </button>
                        </form>
                        <form action={rejectApprovalFromQueue}>
                          <ApprovalDecisionFields approvalId={approval.id} params={params} />
                          <button
                            type="submit"
                            className="action-button secondary-action"
                            disabled={!mutationsEnabled}
                          >
                            Reject
                          </button>
                        </form>
                      </div>
                    ) : null}
                  </div>
                  {approval.decided_by ? (
                    <p className="approval-reason">
                      {formatScenario(approval.status)} by {approval.decided_by}
                      {approval.decided_at ? ` on ${formatDateTime(approval.decided_at)}` : ''}
                      {approval.decision_notes ? `: ${approval.decision_notes}` : ''}
                    </p>
                  ) : null}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}

function ApprovalDecisionFields({
  approvalId,
  params,
}: {
  approvalId: string;
  params: ApprovalSearchParams;
}) {
  return (
    <>
      <input type="hidden" name="approval_id" value={approvalId} />
      {params.status ? <input type="hidden" name="status" value={params.status} /> : null}
      {params.agent_version_id ? (
        <input type="hidden" name="agent_version_id" value={params.agent_version_id} />
      ) : null}
      {params.risk_level ? (
        <input type="hidden" name="risk_level" value={params.risk_level} />
      ) : null}
      {params.include_decided ? (
        <input type="hidden" name="include_decided" value={params.include_decided} />
      ) : null}
    </>
  );
}

async function loadPublishedVersions(): Promise<PublishedVersion[]> {
  const agentsResult = await listAgents({ limit: 100 });
  if (!agentsResult.ok) return [];
  const versionLists = await Promise.all(
    agentsResult.data.agents.map(async (agent) => ({
      agent,
      result: await listAgentVersions(agent.id, { limit: 100 }),
    })),
  );
  return versionLists.flatMap(({ agent, result }) =>
    result.ok
      ? result.data.versions
          .filter((version) => version.status === 'published')
          .map((version) => ({ agent, version }))
      : [],
  );
}

function versionLabel(item: PublishedVersion) {
  return `${item.agent.name} · ${item.version.semantic_version ?? `v${item.version.version_number ?? 'draft'}`}`;
}

function readStatus(value: string | undefined): ApprovalStatus | undefined {
  return value === 'pending' || value === 'approved' || value === 'rejected'
    ? value
    : undefined;
}

function readRiskLevel(value: string | undefined): RiskLevel | undefined {
  return value === 'low' || value === 'high' ? value : undefined;
}
