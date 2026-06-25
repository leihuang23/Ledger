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

  assert.match(source, /Detected revenue anomalies/);
  assert.match(source, /Recent failed invoices/);
  assert.match(source, /Ticket volume by category/);
  assert.match(source, /href="\/evals"/);
  assert.match(source, /href="\/knowledge"/);
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
  assert.match(source, /Cited evidence/);
  assert.match(source, /Approval queue/);
  assert.match(source, /Approve/);
  assert.match(source, /Reject/);
  assert.match(source, /Tool-step history/);
});

test('eval report page exposes scenario scores, failures, and traces', () => {
  const source = readWorkspaceFile('app/evals/page.tsx');

  assert.match(source, /Run Suite/);
  assert.match(source, /Scenario results/);
  assert.match(source, /Expected/);
  assert.match(source, /Actual/);
  assert.match(source, /Observed evidence/);
  assert.match(source, /failure_reasons/);
  assert.match(source, /Trace/);
});
