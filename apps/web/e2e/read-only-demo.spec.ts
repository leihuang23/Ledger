import { expect, test } from '@playwright/test';

test.describe('public read-only demo', () => {
  test.skip(
    process.env.PLAYWRIGHT_EXPECT_READ_ONLY !== 'true',
    'requires a web deployment with OPERATOR_UI_ENABLED=false',
  );

  test('disables mutations while preserving review-only navigation', async ({ page }) => {
    await page.goto('/agents/revenue-ops-agent/versions/revenue-ops-agent_phase6');

    await expect(page.getByText(/public read-only demo/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'New draft from this version' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Launch run' })).toBeDisabled();
    await expect(page.locator('select[name="incident_id"]')).toBeDisabled();

    await page.goto('/evals');
    await expect(page.getByRole('button', { name: 'Run selected dataset' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Compare A vs B' })).toBeEnabled();
  });
});
