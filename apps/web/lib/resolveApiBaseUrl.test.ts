import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveApiBaseUrl } from './resolveApiBaseUrl.ts';

test('resolveApiBaseUrl prefers internal URL during server rendering', () => {
  assert.equal(
    resolveApiBaseUrl({
      isServer: true,
      internalBaseUrl: 'http://api:8000',
      publicBaseUrl: 'http://localhost:8000',
    }),
    'http://api:8000',
  );
});

test('resolveApiBaseUrl falls back to public URL on the server', () => {
  assert.equal(
    resolveApiBaseUrl({
      isServer: true,
      publicBaseUrl: 'http://localhost:8000',
    }),
    'http://localhost:8000',
  );
});

test('resolveApiBaseUrl uses public URL in the browser', () => {
  assert.equal(
    resolveApiBaseUrl({
      isServer: false,
      internalBaseUrl: 'http://api:8000',
      publicBaseUrl: 'http://localhost:8000',
    }),
    'http://localhost:8000',
  );
});
