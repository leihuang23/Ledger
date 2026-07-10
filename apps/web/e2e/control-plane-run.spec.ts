import { expect, test } from '@playwright/test';

test.describe('control-plane run', () => {
  test('launch a run from the agent version page and reach an auditable outcome', async ({ page }) => {
    test.setTimeout(180000);

    // Navigate from the agents registry into the default agent.
    await page.goto('/agents');
    await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();

    // Enter the first (default) agent's detail page.
    // Use the full agent name as the accessible-name filter so the nav brand
    // link ("Ops Agent" -> /) is not matched — a bare /Ops Agent/ alternative
    // would resolve to the brand link first and navigate to the dashboard.
    const agentLink = page.getByRole('link', { name: 'Revenue Ops Agent' }).first();
    await agentLink.click();

    // Pin the stable seeded baseline. Other parallel E2E scenarios publish
    // candidates, so "first/latest Inspect" would make this test order-dependent.
    await page.goto('/agents/revenue-ops-agent/versions/revenue-ops-agent_phase6');

    // The version detail page exposes the Launch form on published versions.
    const launchButton = page.getByRole('button', { name: 'Launch run' });
    await expect(launchButton).toBeVisible();

    // Use a scenario that is distinct from the dashboard demo flow so the two
    // specs remain isolated when Playwright executes them in parallel.
    await page
      .locator('select[name="incident_id"]')
      .selectOption('inc_eval_payment_method_expiration');
    await launchButton.click();

    // The server action redirects to the control-plane run detail page.
    await page.waitForURL(/\/runs\//, { timeout: 30000 });

    // The run executes inline during the server action; poll until it reaches
    // succeeded or waits at the explicit high-risk approval gate. RunRefresh
    // auto-refreshes, but reload to be safe in CI.
    await expect(async () => {
      await page.reload();
      await expect(
        page.locator('.run-status-succeeded, .run-status-waiting_for_approval').first(),
      ).toBeVisible({ timeout: 5000 });
    }).toPass({ timeout: 120000 });

    // The report is available before a possible approval wait, keeping the
    // proposed action and its evidence reviewable at the gate.
    await expect(page.getByRole('heading', { name: 'Root cause' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Cited evidence' })).toBeVisible();

    const rejectButtons = page.getByRole('button', { name: 'Reject', exact: true });
    await expect(rejectButtons).toHaveCount(2);
    for (const remaining of [1, 0]) {
      await rejectButtons.first().click();
      await expect(rejectButtons).toHaveCount(remaining);
    }
  });
});
