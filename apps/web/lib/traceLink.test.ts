import assert from 'node:assert/strict';
import test from 'node:test';

import { localTraceRunPath } from './traceLink.ts';

test('localTraceRunPath parses the API local trace URL format', () => {
  assert.equal(
    localTraceRunPath('local://agent-runs/run_123/traces/local-abc123'),
    '/agent/runs/run_123',
  );
});

test('localTraceRunPath tolerates a missing /traces suffix', () => {
  assert.equal(localTraceRunPath('local://agent-runs/run_123'), '/agent/runs/run_123');
});

test('localTraceRunPath URL-encodes the run id', () => {
  assert.equal(
    localTraceRunPath('local://agent-runs/run%20x/traces/t'),
    '/agent/runs/run%2520x',
  );
  assert.equal(
    localTraceRunPath('local://agent-runs/run x/traces/t'),
    '/agent/runs/run%20x',
  );
});

test('localTraceRunPath rejects hosted provider URLs', () => {
  assert.equal(localTraceRunPath('https://langfuse.example.com/traces/abc'), null);
  assert.equal(localTraceRunPath('http://localhost:3000/traces/abc'), null);
});

test('localTraceRunPath rejects empty, null, and malformed values', () => {
  assert.equal(localTraceRunPath(null), null);
  assert.equal(localTraceRunPath(undefined), null);
  assert.equal(localTraceRunPath(''), null);
  assert.equal(localTraceRunPath('local://agent-runs/'), null);
  assert.equal(localTraceRunPath('local://agent-runs'), null);
  assert.equal(localTraceRunPath('local://other/run_123'), null);
});
