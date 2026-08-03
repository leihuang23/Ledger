import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const appRoot = process.cwd();

function readAppFile(path: string) {
  return readFileSync(join(appRoot, path), 'utf8');
}

test('Cloudflare Worker config preserves the anonymous public demo contract', () => {
  const packageJson = JSON.parse(readAppFile('package.json')) as {
    scripts: Record<string, string>;
  };
  const wranglerConfig = readAppFile('wrangler.jsonc');
  const devVarsExample = readAppFile('.dev.vars.example');

  assert.equal(packageJson.scripts['build:cloudflare'], 'opennextjs-cloudflare build');
  assert.equal(
    packageJson.scripts.preview,
    'opennextjs-cloudflare build && opennextjs-cloudflare preview',
  );
  assert.equal(
    packageJson.scripts.deploy,
    'opennextjs-cloudflare build && opennextjs-cloudflare deploy',
  );

  assert.match(wranglerConfig, /"main": "\.open-next\/worker\.js"/);
  assert.match(wranglerConfig, /"nodejs_compat"/);
  assert.match(wranglerConfig, /"global_fetch_strictly_public"/);
  assert.match(wranglerConfig, /"directory": "\.open-next\/assets"/);
  assert.match(wranglerConfig, /"pattern": "ledger\.leihuang\.me"/);
  assert.match(wranglerConfig, /"custom_domain": true/);
  assert.match(
    wranglerConfig,
    /"preview": \{[\s\S]*?"workers_dev": true,[\s\S]*?"routes": \[\]/,
  );
  assert.match(wranglerConfig, /"API_INTERNAL_BASE_URL": "https:\/\/ledger-api\.onrender\.com"/);
  assert.match(
    wranglerConfig,
    /"NEXT_PUBLIC_API_BASE_URL": "https:\/\/ledger-api\.onrender\.com"/,
  );
  assert.match(wranglerConfig, /"OPERATOR_UI_ENABLED": "false"/);

  assert.match(devVarsExample, /^API_INTERNAL_BASE_URL=http:\/\/localhost:8000$/m);
  assert.match(devVarsExample, /^NEXT_PUBLIC_API_BASE_URL=http:\/\/localhost:8000$/m);
  assert.match(devVarsExample, /^OPERATOR_UI_ENABLED=false$/m);

  const publicConfig = `${wranglerConfig}\n${devVarsExample}`;
  assert.doesNotMatch(
    publicConfig,
    /DEMO_OPERATOR_TOKEN|EVAL_RUN_TOKEN|DOCUMENT_INGEST_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|STRIPE_(?:API_KEY|WEBHOOK_SECRET)/,
  );

  assert.ok(existsSync(join(appRoot, 'open-next.config.ts')));
  assert.ok(existsSync(join(appRoot, 'public/_headers')));
  assert.ok(!existsSync(join(appRoot, 'vercel.json')));
});
