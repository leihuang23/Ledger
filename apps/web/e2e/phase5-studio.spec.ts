import { expect, test } from '@playwright/test';

const apiBaseUrl = process.env.PLAYWRIGHT_API_BASE_URL ?? 'http://localhost:8000';

test.describe('Phase 5 quality controls', () => {
  test('comparison highlights a regression between published versions', async ({ page, request }) => {
    test.setTimeout(600000);
    await page.goto('/evals');
    await expect(page.getByRole('heading', { name: 'Eval Studio' })).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Eval datasets' })).toBeVisible();

    const runVersion = page.locator('select[name="agent_version_id"]');
    const versionA = 'ledger_phase6';
    const versionB = 'ledger_phase6_degraded';
    await expect(runVersion.locator(`option[value="${versionA}"]`)).toHaveCount(1);
    await expect(runVersion.locator(`option[value="${versionB}"]`)).toHaveCount(1);

    // Queue an eval run per version and wait for each to reach a terminal
    // status before asserting the comparison. The two suites share one seeded
    // environment and are serialized by the worker's advisory lock, so on a
    // cold worker they can take longer than a UI poll would tolerate. Waiting
    // on the run status (as the portfolio-readiness suite does) keeps this
    // assertion deterministic regardless of how long the runs take.
    for (const versionId of [versionA, versionB]) {
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
          { timeout: 300000 },
        )
        .toMatch(/passed|failed/);
    }

    await page.goto(`/evals?dataset_id=mrr-drop-suite&version_a=${versionA}&version_b=${versionB}`);
    await expect(page.locator('.regression-banner.has-regressions')).toBeVisible();
    await expect(page.locator('.eval-regression-row').first()).toBeVisible();
    await expect(page.getByText('Regression', { exact: true }).first()).toBeVisible();
  });

  test('approval filters are reflected in the URL and remain selected', async ({ page }) => {
    await page.goto('/approvals');
    await page.locator('select[name="status"]').selectOption('pending');
    await page.locator('select[name="risk_level"]').selectOption('high');

    const versionSelect = page.locator('select[name="agent_version_id"]');
    const versionOption = versionSelect.locator('option').nth(1);
    if ((await versionOption.count()) > 0) {
      const versionId = await versionOption.getAttribute('value');
      if (versionId) await versionSelect.selectOption(versionId);
    }

    await page.getByRole('button', { name: 'Apply filters' }).click();
    await expect(page).toHaveURL(/status=pending/);
    await expect(page).toHaveURL(/risk_level=high/);
    await expect(page.locator('select[name="status"]')).toHaveValue('pending');
    await expect(page.locator('select[name="risk_level"]')).toHaveValue('high');
  });
});
