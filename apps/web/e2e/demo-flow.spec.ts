import { expect, test } from '@playwright/test';

test.describe('demo flow', () => {
  test('dashboard → incident → run → approval → eval report', async ({ page }) => {
    test.setTimeout(180000);
    await page.goto('/');
    await expect(page).toHaveTitle(/Ops Agent/);
    await expect(page.locator('text=Detected revenue anomalies')).toBeVisible();

    // Navigate from dashboard to an incident.
    const incidentLink = page.locator('a:has-text("View incident")').first();
    const openIncidentButton = page.locator('button:has-text("Open incident")').first();
    if (await incidentLink.isVisible().catch(() => false)) {
      await incidentLink.click();
    } else {
      await openIncidentButton.click();
    }
    await expect(page.locator('text=Run investigation')).toBeVisible();

    // Start an inline investigation.
    await page.locator('button:has-text("Run investigation")').click();
    await expect(page.getByRole('heading', { name: 'Root cause' })).toBeVisible({ timeout: 30000 });
    await expect(page.locator('text=succeeded').first()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Cited evidence' })).toBeVisible();

    // Go to the global approvals queue and approve the first pending request.
    await page.getByRole('navigation').getByRole('link', { name: 'Approvals' }).click();
    await page.waitForURL('/approvals');
    await expect(page.getByRole('heading', { name: 'Approval queue' })).toBeVisible();
    const approveButton = page.getByRole('button', { name: 'Approve' }).first();
    await expect(approveButton).toBeVisible();
    await approveButton.click();
    await page.waitForURL('/approvals');
    await expect(page.locator('.action-approved').first()).toBeVisible();

    // Run the eval suite and assert all scenarios pass.
    await page.getByRole('navigation').getByRole('link', { name: 'Evals' }).click();
    await page.waitForURL('/evals');
    await expect(page.getByRole('heading', { name: 'Investigation eval report' })).toBeVisible();
    await page.locator('button:has-text("Run Suite")').click();
    await page.waitForURL('/evals');
    await expect(page.getByText(/\d[\d,]* passed \/ \d[\d,]* failed/)).toBeVisible({
      timeout: 120000,
    });
  });
});
