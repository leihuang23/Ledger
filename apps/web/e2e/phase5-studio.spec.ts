import { expect, test } from '@playwright/test';

test.describe('Phase 5 quality controls', () => {
  test('comparison highlights a regression between published versions', async ({ page }) => {
    test.setTimeout(360000);
    await page.goto('/evals');
    await expect(page.getByRole('heading', { name: 'Eval Studio' })).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Eval datasets' })).toBeVisible();

    const runVersion = page.locator('select[name="agent_version_id"]');
    const versionA = 'revenue-ops-agent_v1';
    const versionB = 'revenue-ops-agent_degraded';
    await expect(runVersion.locator(`option[value="${versionA}"]`)).toHaveCount(1);
    await expect(runVersion.locator(`option[value="${versionB}"]`)).toHaveCount(1);

    await runVersion.selectOption(versionA);
    await page.getByRole('button', { name: 'Run selected dataset' }).click();
    await expect(page.getByText(/Eval run .* queued/)).toBeVisible();

    await page.locator('select[name="agent_version_id"]').selectOption(versionB);
    await page.getByRole('button', { name: 'Run selected dataset' }).click();
    await expect(page.getByText(/Eval run .* queued/)).toBeVisible();

    await page.locator('select[name="version_a"]').selectOption(versionA);
    await page.locator('select[name="version_b"]').selectOption(versionB);
    await page.getByRole('button', { name: 'Compare A vs B' }).click();

    await expect(async () => {
      await page.reload();
      await expect(page.locator('.regression-banner.has-regressions')).toBeVisible({ timeout: 5000 });
      await expect(page.locator('.eval-regression-row').first()).toBeVisible();
      await expect(page.getByText('Regression', { exact: true }).first()).toBeVisible();
    }).toPass({ timeout: 240000 });
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
