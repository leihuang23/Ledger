import { mkdir, rm } from 'node:fs/promises';
import { resolve } from 'node:path';

import { chromium } from 'playwright';

const webBaseUrl = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:3000';
const apiBaseUrl = process.env.PLAYWRIGHT_API_BASE_URL ?? 'http://localhost:8000';
const assetsDir = resolve(process.cwd(), '../../docs/assets');
const videoTmpDir = resolve(assetsDir, '.video-tmp');
const videoPath = resolve(assetsDir, 'ops-agent-walkthrough.webm');
const screenshotPaths = {
  dashboard: resolve(assetsDir, 'control-plane-dashboard.png'),
  evals: resolve(assetsDir, 'eval-regression.png'),
};
const baselineVersionId = 'revenue-ops-agent_phase6';

const operatorToken = process.env.DEMO_OPERATOR_TOKEN;
const evalToken = process.env.EVAL_RUN_TOKEN;

if (!operatorToken || !evalToken) {
  throw new Error(
    'Export DEMO_OPERATOR_TOKEN and EVAL_RUN_TOKEN before capturing the protected operator flow.',
  );
}

async function waitForEvalRun(evalRunId) {
  const deadline = Date.now() + 240_000;
  while (Date.now() < deadline) {
    const response = await fetch(`${apiBaseUrl}/evals/runs/${evalRunId}`);
    if (response.ok) {
      const summary = await response.json();
      if (summary.status === 'passed' || summary.status === 'failed') return summary;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 2_000));
  }
  throw new Error(`Timed out waiting for eval run ${evalRunId}.`);
}

async function waitForRunTerminal(runId) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const response = await fetch(`${apiBaseUrl}/runs/${runId}`);
    if (response.ok) {
      const summary = await response.json();
      if (summary.status === 'succeeded' || summary.status === 'failed') return summary;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 1_000));
  }
  throw new Error(`Timed out waiting for run ${runId} to reach a terminal state.`);
}

async function runEvalFromStudio(page, agentVersionId) {
  await page.goto(
    `${webBaseUrl}/evals?dataset_id=mrr-drop-suite&results_version_id=${encodeURIComponent(agentVersionId)}`,
    { waitUntil: 'networkidle' },
  );
  await page.locator('select[name="agent_version_id"]').selectOption(agentVersionId);
  await Promise.all([
    page.waitForURL((url) => url.searchParams.has('eval_notice')),
    page.getByRole('button', { name: 'Run selected dataset' }).click(),
  ]);
  const notice = new URL(page.url()).searchParams.get('eval_notice');
  const evalRunId = notice?.match(/evalrun_[a-f0-9]+/)?.[0];
  if (!evalRunId) throw new Error(`Could not read eval run id for ${agentVersionId}.`);
  return waitForEvalRun(evalRunId);
}

async function waitForBlockedRun(page) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    if (await page.getByText('Blocked: tool not enabled').first().isVisible()) return;
    await page.waitForTimeout(2_000);
    await page.reload({ waitUntil: 'networkidle' });
  }
  throw new Error('Timed out waiting for the blocked tool step.');
}

async function addSceneLabel(page, title, detail) {
  await page.evaluate(
    ({ sceneTitle, sceneDetail }) => {
      const previous = document.querySelector('[data-portfolio-scene]');
      previous?.remove();
      const label = document.createElement('aside');
      label.dataset.portfolioScene = 'true';
      label.setAttribute(
        'style',
        [
          'position:fixed',
          'z-index:2147483647',
          'left:24px',
          'bottom:24px',
          'max-width:560px',
          'padding:16px 18px',
          'border:1px solid rgba(148,163,184,.45)',
          'border-radius:12px',
          'background:rgba(15,23,42,.94)',
          'box-shadow:0 18px 45px rgba(15,23,42,.28)',
          'color:#f8fafc',
          'font:14px/1.45 ui-sans-serif,system-ui,sans-serif',
        ].join(';'),
      );
      label.innerHTML = `<strong style="display:block;font-size:18px;margin-bottom:4px">${sceneTitle}</strong><span style="color:#cbd5e1">${sceneDetail}</span>`;
      document.body.append(label);
    },
    { sceneTitle: title, sceneDetail: detail },
  );
}

async function scene(
  page,
  path,
  title,
  detail,
  holdMs,
  screenshotPath,
  focusSelector,
) {
  await page.goto(`${webBaseUrl}${path}`, { waitUntil: 'networkidle' });
  if (focusSelector) {
    const focus = page.locator(focusSelector);
    await focus.waitFor({ state: 'visible' });
    await focus.scrollIntoViewIfNeeded();
  }
  if (screenshotPath) {
    await page.screenshot({ path: screenshotPath, fullPage: false });
  }
  await addSceneLabel(page, title, detail);
  await page.waitForTimeout(holdMs);
}

await mkdir(assetsDir, { recursive: true });
await rm(videoTmpDir, { force: true, recursive: true });
await mkdir(videoTmpDir, { recursive: true });
let browser;
let context;
try {
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: videoTmpDir, size: { width: 1440, height: 900 } },
  });
  const page = await context.newPage();
  const video = page.video();

  await scene(
    page,
    '/',
    '1 · Evidence before prose',
    'Deterministic revenue, product, support, and incident signals anchor the investigation.',
    22_000,
  );

  await page.goto(
    `${webBaseUrl}/agents/revenue-ops-agent/versions/${baselineVersionId}`,
    { waitUntil: 'networkidle' },
  );
  await addSceneLabel(
    page,
    '2 · Fork an immutable version',
    'The walkthrough creates a real draft from v1 before changing its governed capabilities.',
  );
  await page.waitForTimeout(12_000);
  const sourceVersionPath = new URL(page.url()).pathname;
  await Promise.all([
    page.waitForURL(
      (url) =>
        url.pathname !== sourceVersionPath &&
        url.pathname.startsWith('/agents/revenue-ops-agent/versions/'),
    ),
    page.getByRole('button', { name: 'New draft from this version' }).click(),
  ]);
  const candidateVersionId = new URL(page.url()).pathname.split('/').at(-1);
  if (!candidateVersionId) throw new Error('Draft version id was not present in the URL.');

  await page.locator('input[name="enabled_tool_ids"][value="search_docs"]').uncheck();
  await page.locator('input[name="enabled_tool_ids"][value="run_eval"]').check();
  await page.locator('input[name="allowed_scopes"][value="run_eval"]').check();
  await addSceneLabel(
    page,
    '3 · Attach tools and scopes',
    'The candidate keeps eval permission but removes document search so the blocked call is observable.',
  );
  await page.waitForTimeout(12_000);
  await page.getByRole('button', { name: 'Save draft' }).click();
  await page.getByText('Draft saved successfully.').waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Publish version' }).click();
  await page.waitForURL(/\/agents\/revenue-ops-agent\?version_published=/);

  await scene(
    page,
    '/tools',
    '4 · Governed tool registry',
    'Every attachable binding has typed schemas, an audited implementation, and one fixed scope.',
    18_000,
  );

  await page.goto(
    `${webBaseUrl}/agents/revenue-ops-agent/versions/${encodeURIComponent(candidateVersionId)}`,
    { waitUntil: 'networkidle' },
  );
  await page.locator('select[name="incident_id"]').selectOption('inc_rev_mrr_wow_drop_20260603');
  await page.getByRole('button', { name: 'Launch run' }).click();
  await page.waitForURL(/\/runs\//);
  const runId = new URL(page.url()).pathname.split('/').at(-1);
  if (!runId) throw new Error('Run id was not present in the launched run URL.');
  await waitForBlockedRun(page);
  await addSceneLabel(
    page,
    '5 · Launch and audit the run',
    'The published version, blocked tool, citations, trace, token estimate, and cost are persisted together.',
  );
  await page.waitForTimeout(25_000);

  await page.goto(
    `${webBaseUrl}/approvals?agent_version_id=${encodeURIComponent(candidateVersionId)}`,
    { waitUntil: 'networkidle' },
  );
  let decisionCount = 0;
  while (await page.getByRole('button', { name: 'Reject', exact: true }).count()) {
    if (decisionCount >= 4) {
      throw new Error(`Run ${runId} exceeded the approval-decision safety cap.`);
    }
    await page.getByRole('button', { name: 'Reject', exact: true }).first().click();
    await page.waitForTimeout(500);
    await page.reload({ waitUntil: 'networkidle' });
    decisionCount += 1;
  }
  if (decisionCount !== 2) {
    throw new Error(`Run ${runId} exposed ${decisionCount} pending approvals; expected 2.`);
  }
  const completedRun = await waitForRunTerminal(runId);
  if (completedRun.status !== 'succeeded') {
    throw new Error(`Run ${runId} ended with unexpected status ${completedRun.status}.`);
  }
  await addSceneLabel(
    page,
    '6 · Record an approval decision',
    'A customer-facing draft stays mocked; the operator decision and audit event remain inspectable.',
  );
  await page.waitForTimeout(15_000);

  await scene(
    page,
    '/dashboard',
    '7 · Trace, cost, and latency',
    'Per-version success rate, p95 latency, and estimated cost drill back into individual runs.',
    20_000,
    screenshotPaths.dashboard,
    '.snapshot-bar',
  );

  await page.goto(`${webBaseUrl}/evals?dataset_id=mrr-drop-suite`, {
    waitUntil: 'networkidle',
  });
  await addSceneLabel(
    page,
    '8 · Trigger the baseline eval',
    'The recording launches a fresh six-case dataset run instead of relying on precomputed rows.',
  );
  await page.waitForTimeout(10_000);
  await runEvalFromStudio(page, baselineVersionId);
  await addSceneLabel(
    page,
    '9 · Trigger the candidate eval',
    'A second fresh run evaluates the version created earlier in this walkthrough.',
  );
  await page.waitForTimeout(10_000);
  await runEvalFromStudio(page, candidateVersionId);

  const comparisonPath =
    `/evals?dataset_id=mrr-drop-suite&results_version_id=${encodeURIComponent(candidateVersionId)}` +
    `&version_a=${baselineVersionId}&version_b=${encodeURIComponent(candidateVersionId)}`;
  await page.goto(`${webBaseUrl}${comparisonPath}`, { waitUntil: 'networkidle' });
  await page.getByText(/regressions? detected/).waitFor({ state: 'visible' });
  await page.locator('.eval-comparison-panel').scrollIntoViewIfNeeded();
  await page.screenshot({ path: screenshotPaths.evals, fullPage: false });
  await addSceneLabel(
    page,
    '10 · Inspect the failed case',
    'The missing document-search capability flips at least one baseline pass into a visible regression.',
  );
  await page.waitForTimeout(38_000);

  await context.close();
  context = undefined;
  if (!video) throw new Error('Playwright did not create a walkthrough recording.');
  await video.saveAs(videoPath);
} finally {
  if (context) await context.close();
  if (browser) await browser.close();
  await rm(videoTmpDir, { force: true, recursive: true });
}

process.stdout.write(
  `${JSON.stringify({ video: videoPath, screenshots: screenshotPaths }, null, 2)}\n`,
);
