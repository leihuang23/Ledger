import { expect, test } from '@playwright/test';

test.describe('failure flow', () => {
  test('low-confidence report + rejected approval stays rejected', async ({ page }) => {
    test.setTimeout(180000);

    // 1. Navigate to the incidents list and open the ambiguous/unknown-root-cause
    //    incident (6th scenario). Its title is distinct from the deterministic
    //    scenarios, so we can target it directly.
    await page.goto('/incidents');
    await expect(page.getByRole('heading', { name: 'Incidents', exact: true })).toBeVisible();

    const ambiguousIncident = page.getByRole('link', {
      name: 'Paid MRR dropped with mixed renewal signals across accounts',
    });
    await ambiguousIncident.click();

    // 2. Start an inline investigation.
    await expect(page.locator('button:has-text("Run investigation")')).toBeVisible();
    await page.locator('button:has-text("Run investigation")').click();

    // 3. Wait for the report to render. The ambiguous scenario should produce
    //    a low or medium confidence report (not high).
    await expect(page.getByRole('heading', { name: 'Root cause' })).toBeVisible({
      timeout: 30000,
    });

    // The confidence pill must NOT be "high" — the ambiguous scenario should
    // yield low or medium confidence, proving the agent reports uncertainty
    // rather than hallucinating a specific root cause.
    const confidencePill = page.locator('[class*="confidence-pill"]');
    await expect(confidencePill).toBeVisible();
    const confidenceText = (await confidencePill.textContent()) ?? '';
    expect(confidenceText.toLowerCase()).not.toContain('high');

    // 4. Navigate to the approvals queue and reject the first pending request.
    await page.getByRole('navigation').getByRole('link', { name: 'Approvals' }).click();
    await page.waitForURL('/approvals');
    await expect(page.getByRole('heading', { name: 'Approval queue' })).toBeVisible();

    const rejectButton = page.getByRole('button', { name: 'Reject' }).first();
    await expect(rejectButton).toBeVisible();
    await rejectButton.click();
    await page.waitForURL('/approvals');

    // 5. The rejected approval must stay rejected. The status badge should show
    //    "rejected" and the Approve/Reject buttons must be gone (only pending
    //    approvals show action buttons).
    await expect(page.locator('.action-rejected').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reject' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  });
});
