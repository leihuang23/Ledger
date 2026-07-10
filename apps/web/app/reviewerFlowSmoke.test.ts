import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const appRoot = process.cwd();

function readWorkspaceFile(path: string) {
  return readFileSync(join(appRoot, path), 'utf8');
}

test('dashboard page exposes anomaly and navigation review surfaces', () => {
  const source = readWorkspaceFile('app/page.tsx');
  const nav = readWorkspaceFile('app/Nav.tsx');

  assert.match(source, /Detected revenue anomalies/);
  assert.match(source, /Seeded scenario coverage/);
  assert.match(source, /Recent failed invoices/);
  assert.match(source, /Ticket volume by category/);
  assert.match(nav, /href[:=]\s*['"]\/incidents['"]/);
  assert.match(nav, /href[:=]\s*['"]\/agent\/runs['"]/);
  assert.match(nav, /href[:=]\s*['"]\/approvals['"]/);
  assert.match(nav, /href[:=]\s*['"]\/knowledge['"]/);
  assert.match(nav, /href[:=]\s*['"]\/evals['"]/);
  assert.match(nav, /href[:=]\s*['"]\/tools['"]/);
});

test('tool registry exposes governed scopes and expandable input/output schemas', () => {
  const source = readWorkspaceFile('app/tools/page.tsx');
  const apiSource = readWorkspaceFile('lib/api.ts');

  assert.match(source, /Tool registry/);
  assert.match(source, /Permission scope/);
  assert.match(source, /Input schema/);
  assert.match(source, /Output schema/);
  assert.match(source, /implementation_ref/);
  assert.match(source, /<details/);
  assert.match(apiSource, /\/tools/);
  assert.match(apiSource, /input_schema/);
  assert.match(apiSource, /output_schema/);
});

test('incident page exposes evidence needed before launching an investigation', () => {
  const source = readWorkspaceFile('app/incidents/[incidentId]/page.tsx');

  assert.match(source, /Run investigation/);
  assert.match(source, /Metric evidence/);
  assert.match(source, /Affected accounts/);
  assert.match(source, /Support signals/);
  assert.match(source, /Product signals/);
  assert.match(source, /Evidence sources/);
});

test('agent run page exposes report, trace, cost, citations, approvals, and step history', () => {
  const source = readWorkspaceFile('app/agent/runs/[runId]/page.tsx');

  assert.match(source, /Root cause/);
  assert.match(source, /Trace/);
  assert.match(source, /Estimated tokens/);
  assert.match(source, /Estimated cost/);
  assert.match(source, /Claim citations/);
  assert.match(source, /Cited evidence/);
  assert.match(source, /Approval queue/);
  assert.match(source, /Approve/);
  assert.match(source, /Reject/);
  assert.match(source, /Tool-step history/);
});

test('server actions redirect safely unless the protected operator UI is explicitly enabled', () => {
  const apiSource = readWorkspaceFile('lib/api.ts');
  const actionSource = readWorkspaceFile('app/actions.ts');
  const operatorSource = readWorkspaceFile('lib/operatorMutations.ts');
  const layoutSource = readWorkspaceFile('app/layout.tsx');
  const dashboardSource = readWorkspaceFile('app/page.tsx');

  assert.match(apiSource, /X-Demo-Operator-Token/);
  assert.doesNotMatch(apiSource, /process\.env\.DEMO_OPERATOR_TOKEN/);
  assert.match(actionSource, /process\.env\.DEMO_OPERATOR_TOKEN/);
  assert.match(actionSource, /requireOperatorMutationsEnabled\(\)/);
  const exportedActions = actionSource.match(/^export async function /gm) ?? [];
  const mutationGuards = actionSource.match(/requireOperatorMutationsEnabled\(\);/g) ?? [];
  assert.equal(mutationGuards.length, exportedActions.length);
  assert.doesNotMatch(actionSource, /NEXT_PUBLIC_DEMO_OPERATOR_TOKEN/);
  assert.match(operatorSource, /OPERATOR_UI_ENABLED/);
  assert.match(operatorSource, /READ_ONLY_OPERATOR_DESTINATION = '\/\?read_only=1'/);
  assert.match(operatorSource, /redirect\(READ_ONLY_OPERATOR_DESTINATION\)/);
  assert.doesNotMatch(operatorSource, /throw new Error/);
  assert.match(layoutSource, /Public read-only demo/);
  assert.match(dashboardSource, /No operator action was performed/);
});

test('public server-rendered mutation controls are read-only while GET review forms stay active', () => {
  const mutationSurfaces: Array<[string, string[]]> = [
    ['app/page.tsx', ['openIncidentFromAnomaly']],
    ['app/incidents/[incidentId]/page.tsx', ['startInvestigationFromIncident']],
    ['app/agents/[agentId]/page.tsx', ['saveAgentVersionDraft']],
    [
      'app/agents/[agentId]/versions/[versionId]/page.tsx',
      ['launchControlPlaneRun', 'publishAgentVersion', 'saveAgentVersionDraft'],
    ],
    ['app/evals/page.tsx', ['runEvalDatasetFromStudio']],
    ['app/approvals/page.tsx', ['approveApprovalFromQueue', 'rejectApprovalFromQueue']],
    ['app/runs/[runId]/page.tsx', ['approveApprovalFromRun', 'rejectApprovalFromRun']],
    ['app/agent/runs/[runId]/page.tsx', ['approveApprovalFromRun', 'rejectApprovalFromRun']],
  ];

  for (const [path, actionNames] of mutationSurfaces) {
    const source = readWorkspaceFile(path);
    assert.match(source, /operatorMutationsEnabled/);
    assert.match(source, /ReadOnlyOperatorNotice/);
    for (const actionName of actionNames) {
      const formPattern = new RegExp(
        `<form action=\\{${actionName}\\}[\\s\\S]*?<\\/form>`,
        'g',
      );
      const forms = source.match(formPattern) ?? [];
      assert.ok(forms.length > 0, `${path} must render ${actionName}`);
      for (const form of forms) {
        assert.match(form, /disabled=\{!mutationsEnabled/);
      }
    }
  }

  const noticeSource = readWorkspaceFile('app/ReadOnlyOperatorNotice.tsx');
  assert.match(noticeSource, /public read-only demo/);
  assert.match(noticeSource, /protected operator\s+deployment/);

  const approvalSource = readWorkspaceFile('app/approvals/page.tsx');
  const approvalFilterForm = approvalSource.match(/<form action="\/approvals"[\s\S]*?<\/form>/)?.[0];
  assert.ok(approvalFilterForm);
  assert.match(approvalFilterForm, /method="get"/);
  assert.doesNotMatch(approvalFilterForm, /disabled=\{!mutationsEnabled/);

  const evalSource = readWorkspaceFile('app/evals/page.tsx');
  const evalCompareForm = evalSource.match(/<form action="\/evals"[\s\S]*?<\/form>/)?.[0];
  assert.ok(evalCompareForm);
  assert.match(evalCompareForm, /method="get"/);
  assert.doesNotMatch(evalCompareForm, /disabled=\{!mutationsEnabled/);
});

test('stateful portfolio suite disables retries at suite scope', () => {
  const source = readWorkspaceFile('e2e/portfolio-readiness.spec.ts');

  assert.match(source, /test\.describe\.serial/);
  assert.match(source, /test\.describe\.configure\(\{ retries: 0 \}\)/);
  assert.match(source, /mutates one shared portfolio environment/);
});

test('eval studio exposes datasets, per-version runs, results, and regression comparison', () => {
  const source = readWorkspaceFile('app/evals/page.tsx');

  assert.match(source, /Eval Studio/);
  assert.match(source, /Datasets/);
  assert.match(source, /Run selected dataset/);
  assert.match(source, /Compare A vs B/);
  assert.match(source, /Per-case results/);
  assert.match(source, /regressions detected/);
  assert.match(source, /eval-regression-row/);
});

test('eval api client uses the Phase 5 dataset, result, and comparison contracts', () => {
  const source = readWorkspaceFile('lib/api.ts');

  assert.match(source, /\/eval-datasets/);
  assert.match(source, /agent_version_id: agentVersionId/);
  assert.match(source, /\/eval-results/);
  assert.match(source, /\/eval-results\/compare/);
  assert.match(source, /dataset_id/);
  assert.match(source, /version_a/);
  assert.match(source, /version_b/);
});

test('approval queue filters by version and risk and preserves filters through decisions', () => {
  const pageSource = readWorkspaceFile('app/approvals/page.tsx');
  const actionSource = readWorkspaceFile('app/actions.ts');

  assert.match(pageSource, /name="agent_version_id"/);
  assert.match(pageSource, /name="risk_level"/);
  assert.match(pageSource, /ApprovalDecisionFields/);
  assert.match(actionSource, /copySafeQueryValue\(formData, params, 'agent_version_id'\)/);
  assert.match(actionSource, /copySafeQueryValue\(formData, params, 'risk_level'\)/);
});
