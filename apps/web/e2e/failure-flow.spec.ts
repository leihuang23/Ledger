import { expect, test } from '@playwright/test';

test.describe('failure flow', () => {
  test('low-confidence report + rejected approval stays rejected', async ({ page, request }) => {
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

    // 2. Start a fresh inline investigation. The backend intentionally reuses
    // successful runs by default, so force a new run to avoid stale approval
    // state from prior specs or local smoke runs.
    const apiBaseURL = process.env.PLAYWRIGHT_API_BASE_URL || 'http://localhost:8000';
    const operatorToken = process.env.DEMO_OPERATOR_TOKEN;
    const runResponse = await request.post(`${apiBaseURL}/agent/investigations`, {
      headers: operatorToken
        ? { 'X-Demo-Operator-Token': operatorToken }
        : undefined,
      data: {
        incident_id: 'inc_eval_unknown_root_cause',
        agent_version_id: 'revenue-ops-agent_phase6',
        run_inline: true,
        force: true,
      },
    });
    expect(runResponse.ok()).toBeTruthy();
    const run = await runResponse.json();
    await page.goto(`/agent/runs/${run.id}`);

    // 3. Wait for the report to render. The ambiguous scenario should produce
    //    a low or medium confidence report (not high).
    await expect(page.getByRole('heading', { name: 'Root cause', exact: true })).toBeVisible({
      timeout: 30000,
    });

    // The confidence pill must NOT be "high" — the ambiguous scenario should
    // yield low or medium confidence, proving the agent reports uncertainty
    // rather than hallucinating a specific root cause.
    const confidencePill = page.locator('[class*="confidence-pill"]');
    await expect(confidencePill).toBeVisible();
    const confidenceText = (await confidencePill.textContent()) ?? '';
    expect(confidenceText.toLowerCase()).not.toContain('high');

    // 4. Reject the pending request that belongs to this investigation run.
    // The full demo flow can create other pending approvals via eval runs, so
    // keep this assertion scoped to the current run instead of the global queue.
    const runApprovalPanel = page.locator('section.approval-panel');
    await expect(
      runApprovalPanel.getByRole('heading', { name: 'Approval queue' }),
    ).toBeVisible();

    const rejectButton = runApprovalPanel.getByRole('button', { name: 'Reject' }).first();
    await expect(rejectButton).toBeVisible();
    const pendingAction = rejectButton.locator('xpath=ancestor::article[contains(@class, "approval-row")]');
    const actionTitle = await pendingAction.locator('h3').innerText();
    await rejectButton.click();

    // 5. The rejected approval must stay rejected for this run. Its status
    // badge should show "rejected" and that action's Approve/Reject buttons
    // must be gone. Other high-risk actions in the same run may remain pending.
    const rejectedAction = runApprovalPanel.locator('article.approval-row').filter({
      has: page.getByRole('heading', { name: actionTitle }),
    });
    await expect(rejectedAction.locator('.action-rejected')).toBeVisible();
    await expect(rejectedAction.getByRole('button', { name: 'Reject' })).toHaveCount(0);
    await expect(rejectedAction.getByRole('button', { name: 'Approve' })).toHaveCount(0);

    const remainingReject = runApprovalPanel.getByRole('button', {
      name: 'Reject',
      exact: true,
    });
    await expect(remainingReject).toHaveCount(1);
    await remainingReject.click();
    await expect(remainingReject).toHaveCount(0);
  });
});
