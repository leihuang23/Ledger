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
    const readIncidentCount = async () => {
      const response = await request.get(`${apiBaseUrl}/incidents?limit=100`);
      expect(response.ok()).toBeTruthy();
      const body = (await response.json()) as { incidents?: unknown[] };
      return body.incidents?.length ?? 0;
    };
    const readRunCount = async () => {
      const response = await request.get(`${apiBaseUrl}/runs?limit=100`);
      expect(response.ok()).toBeTruthy();
      const body = (await response.json()) as unknown[];
      return Array.isArray(body) ? body.length : 0;
    };
    const beforeIncidentCount = await readIncidentCount();
    const beforeRunCount = await readRunCount();

    // Open the first seeded incident. The investigation form is rendered but its
    // submit control is disabled in the read-only demo.
    await page.goto('/incidents');
    const incidentLink = page.locator('a[href^="/incidents/"]').first();
    await expect(incidentLink).toBeVisible();
    await incidentLink.click();
    await page.waitForURL(/\/incidents\//);
    const runButton = page.getByRole('button', { name: 'Run investigation' });
    await expect(runButton).toBeDisabled();

    // Forge the operator mutation: re-enable the disabled server-action submit
    // button and post the form exactly as an operator would.
    await page.locator('form.investigation-form').evaluate((form) => {
      const button = form.querySelector('button[type="submit"]');
      if (button instanceof HTMLButtonElement) {
        button.disabled = false;
      }
    });
    await expect(runButton).toBeEnabled();
    await runButton.click();

    // The server action must fail closed: redirect to the controlled read-only
    // destination instead of launching an investigation.
    await page.waitForURL(/\?read_only=1/, { timeout: 15000 });
    await expect(page.getByText(/No operator action was performed/i)).toBeVisible();

    // No API state may change as a result of the forged mutation.
    expect(await readIncidentCount()).toBe(beforeIncidentCount);
    expect(await readRunCount()).toBe(beforeRunCount);
  });
});
