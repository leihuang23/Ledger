import { expect, test } from '@playwright/test';

test.describe('control-plane run', () => {
  test('launch a run from the agent version page and reach succeeded', async ({ page }) => {
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

    // Enter the latest published version (v1) from the agent's version list.
    // The version row renders "v1" as text and an "Inspect" link into the
    // version detail page (where the Launch form lives).
    const versionLink = page.getByRole('link', { name: 'Inspect' }).first();
    await versionLink.click();

    // The version detail page exposes the Launch form on published versions.
    const launchButton = page.getByRole('button', { name: 'Launch run' });
    await expect(launchButton).toBeVisible();

    // The seeded canonical incident is the default option; submit the form.
    await launchButton.click();

    // The server action redirects to the control-plane run detail page.
    await page.waitForURL(/\/runs\//, { timeout: 30000 });

    // The run executes inline during the server action; poll until it reaches
    // succeeded (RunRefresh auto-refreshes, but reload to be safe in CI).
    await expect(async () => {
      await page.reload();
      await expect(page.locator('.run-status-succeeded').first()).toBeVisible({
        timeout: 5000,
      });
    }).toPass({ timeout: 120000 });

    // The final report's root cause is surfaced once the run succeeds.
    await expect(page.getByRole('heading', { name: 'Root cause' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Cited evidence' })).toBeVisible();
  });
});
