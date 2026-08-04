import { expect, test } from '@playwright/test';

/**
 * Anonymous browse path for the public read-only demo.
 * Enabled only when PLAYWRIGHT_EXPECT_READ_ONLY=true (OPERATOR_UI_ENABLED=false).
 */
test.describe('public demo browse', () => {
  test.skip(
    process.env.PLAYWRIGHT_EXPECT_READ_ONLY !== 'true',
    'requires a web deployment with OPERATOR_UI_ENABLED=false',
  );

  test('anonymous visitor can inspect seeded evidence surfaces', async ({ page }) => {
    test.setTimeout(120000);

    await page.goto('/');
    await expect(page).toHaveTitle(/Ledger/);
    await expect(page.getByText(/public read-only demo/i).first()).toBeVisible();
    await expect(page.getByText(/Detected revenue anomalies/i)).toBeVisible();
    await expect(page.getByText(/API online/i)).toBeVisible();

    // Incidents list / detail
    await page.getByRole('navigation').getByRole('link', { name: 'Incidents' }).click();
    await page.waitForURL(/\/incidents/);
    const incidentLink = page.locator('a[href^="/incidents/"]').first();
    await expect(incidentLink).toBeVisible();
    await incidentLink.click();
    await expect(page.getByText(/Metric evidence|Affected accounts|Evidence sources/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /Run investigation/i })).toBeDisabled();

    // Runs timeline shows seeded completed runs with ordered steps
    await page.getByRole('navigation').getByRole('link', { name: 'Runs', exact: true }).click();
    await page.waitForURL(/\/runs/);
    await expect(page.getByRole('heading', { name: /Runs/i }).first()).toBeVisible();
    const runLink = page.locator('a[href^="/runs/"]').first();
    await expect(runLink).toBeVisible();
    await runLink.click();
    await page.waitForURL(/\/runs\/run_/);
    await expect(
      page.getByRole('heading', { name: /Cited evidence|Tool-step history/i }).first(),
    ).toBeVisible();
    await page.goBack();

    // Approvals queue shows seeded approval states (read-only inspection)
    await page.getByRole('navigation').getByRole('link', { name: 'Approvals' }).click();
    await page.waitForURL('/approvals');
    await expect(page.getByRole('heading', { name: /Approval/i }).first()).toBeVisible();
    await expect(page.locator('.approval-row').first()).toBeVisible();

    // Evals studio shows seeded regression results; mutating run control is disabled
    await page.getByRole('navigation').getByRole('link', { name: 'Evals' }).click();
    await page.waitForURL('/evals');
    // The public demo API can cold-start on the free Render tier immediately after deploy.
    await expect(page.getByRole('heading', { name: /Eval/i }).first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText(/regression|pass rate|eval/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Run selected dataset' })).toBeDisabled();

    // Agents + tools control-plane surfaces
    await page.getByRole('navigation').getByRole('link', { name: 'Agents' }).click();
    await page.waitForURL('/agents');
    await expect(page.getByRole('heading', { name: /Agents/i }).first()).toBeVisible();

    await page.getByRole('navigation').getByRole('link', { name: 'Tools' }).click();
    await page.waitForURL('/tools');
    await expect(page.getByText(/Tool registry|Permission scope/i).first()).toBeVisible();

    // Observability dashboard
    await page.goto('/dashboard');
    await expect(
      page.getByRole('heading', { name: /Trace, cost|Dashboard|latency/i }).first(),
    ).toBeVisible();
  });

  test('HTML responses do not embed operator or model secret env names', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.ok()).toBeTruthy();
    const html = await page.content();
    expect(html).not.toMatch(/DEMO_OPERATOR_TOKEN\s*=/);
    expect(html).not.toMatch(/EVAL_RUN_TOKEN\s*=/);
    expect(html).not.toMatch(/DOCUMENT_INGEST_TOKEN\s*=/);
    expect(html).not.toMatch(/OPENAI_API_KEY\s*=/);
    expect(html).not.toMatch(/ANTHROPIC_API_KEY\s*=/);
    expect(html).not.toMatch(/sk_live_/);
    expect(html).not.toMatch(/sk_test_[a-zA-Z0-9]{8,}/);
  });
});
