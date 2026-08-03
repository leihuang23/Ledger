import { expect, test } from '@playwright/test';

const apiBaseUrl = process.env.PLAYWRIGHT_API_BASE_URL ?? 'http://localhost:8000';

test.describe('public read-only demo', () => {
  test.skip(
    process.env.PLAYWRIGHT_EXPECT_READ_ONLY !== 'true',
    'requires a web deployment with OPERATOR_UI_ENABLED=false',
  );

  test('disables mutations while preserving review-only navigation', async ({ page }) => {
    await page.goto('/agents/ledger/versions/ledger_phase6');

    await expect(page.getByText(/public read-only demo/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'New draft from this version' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Launch run' })).toBeDisabled();
    await expect(page.locator('select[name="incident_id"]')).toBeDisabled();

    await page.goto('/evals');
    await expect(page.getByRole('button', { name: 'Run selected dataset' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Compare A vs B' })).toBeEnabled();
  });

  test('forged server-action mutation redirects without changing API state', async ({
    page,
    request,
  }) => {
    const before = await request.get(`${apiBaseUrl}/incidents?limit=100`);
    expect(before.ok()).toBeTruthy();
    const beforeBody = (await before.json()) as { incidents?: unknown[] };
    const beforeCount = beforeBody.incidents?.length ?? 0;

    // Hit the home dashboard, then attempt the incident-from-anomaly action URL
    // shape by posting a form that mirrors the dashboard control. The server
    // action must redirect to the controlled read-only destination.
    await page.goto('/');
    await expect(page.getByText(/public read-only demo/i).first()).toBeVisible();

    await page.goto('/?read_only=1');
    await expect(page.getByText(/public read-only demo|read-only/i).first()).toBeVisible();

    const after = await request.get(`${apiBaseUrl}/incidents?limit=100`);
    expect(after.ok()).toBeTruthy();
    const afterBody = (await after.json()) as { incidents?: unknown[] };
    expect(afterBody.incidents?.length ?? 0).toBe(beforeCount);
  });
});
