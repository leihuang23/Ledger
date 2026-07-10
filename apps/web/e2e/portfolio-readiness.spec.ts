import { expect, test } from '@playwright/test';

const apiBaseUrl = process.env.PLAYWRIGHT_API_BASE_URL ?? 'http://localhost:8000';
const baselineVersionId = 'revenue-ops-agent_phase6';

test.describe.serial('Phase 6 portfolio readiness', () => {
  // This suite mutates one shared portfolio environment across tests. Retrying a
  // partially completed serial suite would reuse published versions and approval
  // decisions, obscuring the original failure with state left by the first attempt.
  test.describe.configure({ retries: 0 });

  let reviewedVersionId = '';

  test('publish a governed version and expose its blocked tool step', async ({ page, request }) => {
    test.setTimeout(180000);

    await page.goto(`/agents/revenue-ops-agent/versions/${baselineVersionId}`);
    const sourcePath = new URL(page.url()).pathname;
    await Promise.all([
      page.waitForURL(
        (url) =>
          url.pathname !== sourcePath &&
          url.pathname.startsWith('/agents/revenue-ops-agent/versions/'),
      ),
      page.getByRole('button', { name: 'New draft from this version' }).click(),
    ]);

    const draftUrl = new URL(page.url());
    const versionId = draftUrl.pathname.split('/').at(-1);
    expect(versionId).toBeTruthy();
    reviewedVersionId = versionId ?? '';

    const searchDocs = page.locator('input[name="enabled_tool_ids"][value="search_docs"]');
    await expect(searchDocs).toBeChecked();
    await searchDocs.uncheck();
    await page.getByRole('button', { name: 'Save draft' }).click();
    await expect(page.getByText('Draft saved successfully.')).toBeVisible();

    await page.getByRole('button', { name: 'Publish version' }).click();
    await page.waitForURL(/\/agents\/revenue-ops-agent\?version_published=/);

    await page.goto(`/agents/revenue-ops-agent/versions/${versionId}`);
    await expect(page.getByText('published', { exact: true }).first()).toBeVisible();
    await page.locator('select[name="incident_id"]').selectOption('inc_eval_enterprise_churn_wave');
    await page.getByRole('button', { name: 'Launch run' }).click();
    await page.waitForURL(/\/runs\//);
    const runId = new URL(page.url()).pathname.split('/').at(-1);
    expect(runId).toBeTruthy();

    await expect(async () => {
      await page.reload();
      await expect(page.getByText('Blocked: tool not enabled').first()).toBeVisible({
        timeout: 5000,
      });
    }).toPass({ timeout: 120000 });

    await page.goto(`/approvals?agent_version_id=${encodeURIComponent(reviewedVersionId)}`);
    for (let index = 0; index < 2; index += 1) {
      const reject = page.getByRole('button', { name: 'Reject', exact: true }).first();
      await expect(reject).toBeVisible();
      await reject.click();
      await page.waitForLoadState('networkidle');
      await page.reload();
    }
    await expect(page.getByRole('button', { name: 'Reject', exact: true })).toHaveCount(0);
    await expect
      .poll(async () => {
        const response = await request.get(`${apiBaseUrl}/runs/${runId}`);
        if (!response.ok()) return 'pending';
        return (await response.json()).status as string;
      })
      .toBe('succeeded');
  });

  test('run the governed version eval and expose its regression', async ({ page, request }) => {
    test.setTimeout(240000);
    expect(reviewedVersionId).toBeTruthy();

    for (const versionId of [baselineVersionId, reviewedVersionId]) {
      await page.goto(
        `/evals?dataset_id=mrr-drop-suite&results_version_id=${encodeURIComponent(versionId)}`,
      );
      await page.locator('select[name="agent_version_id"]').selectOption(versionId);
      await Promise.all([
        page.waitForURL((url) => url.searchParams.has('eval_notice')),
        page.getByRole('button', { name: 'Run selected dataset' }).click(),
      ]);
      const notice = new URL(page.url()).searchParams.get('eval_notice');
      const evalRunId = notice?.match(/evalrun_[a-f0-9]+/)?.[0];
      expect(evalRunId).toBeTruthy();

      await expect
        .poll(
          async () => {
            const response = await request.get(`${apiBaseUrl}/evals/runs/${evalRunId}`);
            if (!response.ok()) return 'pending';
            return (await response.json()).status as string;
          },
          { timeout: 180000 },
        )
        .toMatch(/passed|failed/);
    }

    await page.goto(
      `/evals?dataset_id=mrr-drop-suite&results_version_id=${encodeURIComponent(reviewedVersionId)}` +
        `&version_a=${baselineVersionId}&version_b=${encodeURIComponent(reviewedVersionId)}`,
    );
    await expect(page.getByText(/regressions? detected/)).toBeVisible();
    await expect(page.locator('.eval-regression-row').first()).toBeVisible();
  });

  test('observability dashboard exposes version metrics and trace drill-down', async ({ page }) => {
    await page.goto('/dashboard');

    await expect(page.getByRole('heading', { name: 'Trace, cost & latency' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Per-agent-version aggregates' })).toBeVisible();
    await expect(page.getByText('Total estimated cost')).toBeVisible();
    await expect(page.getByText(/Cost values are estimates/)).toBeVisible();
    await expect(page.getByRole('link', { name: 'Runs' }).first()).toBeVisible();

    await page.getByRole('link', { name: 'Run timeline' }).click();
    await expect(page.getByRole('heading', { name: 'Runs', exact: true })).toBeVisible();
    await expect(page.locator('tbody tr').first()).toBeVisible();
  });
});
