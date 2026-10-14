import { expect, test } from '@playwright/test';

const briefing = {
  version: 'war-room-foundation-v1', as_of_at: new Date().toISOString(), timezone: 'America/Chicago',
  today_count: 0, observed_market_games: 0, verified_bet365_count: 0, qualified_count: 0,
  decision: 'NO_BET', briefing: 'No verified bet365 Tennessee opportunity qualifies.',
  latest_run: null, changes: [], games: [],
};
// Contract fixtures only; these never enter the application's real research store.
const report = {
  status: 'RESEARCH ONLY', report: {
    run_id: 'test-comparison', created_at: '2026-10-14T12:00:00Z', scope: 'Retrospective test fixture',
    limitations: ['Previously inspected seasons are not an untouched holdout.'], recommended_for_review: [], estimates: [],
    models: [{ name: 'test-rating', status: 'VALIDATION FAILED', n: 100,
      metrics: { brier: 0.24, log_loss: 0.68, mae_margin: 12, mae_total: 11 },
      reasons: ['Did not improve the baseline.'] }],
  },
};

test.beforeEach(async ({ page }) => {
  await page.route('**/research-api/v1/research/briefing', route => route.fulfill({ json: briefing }));
});

test('model comparison preserves failed evidence and NO BET on mobile', async ({ page }) => {
  await page.route('**/research-api/v1/research/model-comparison', route => route.fulfill({ json: report }));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toBeVisible();
  await page.getByText('Model results and limitations (1 approaches)', { exact: true }).click();
  await expect(page.getByText('VALIDATION FAILED · 100 games')).toBeVisible();
  await expect(page.getByText('Did not improve the baseline.')).toBeVisible();
  await expect(page.getByText('Previously inspected seasons are not an untouched holdout.')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('failed model refresh hides retained numerical findings', async ({ page }) => {
  let failed = false;
  await page.route('**/research-api/v1/research/model-comparison', route => failed
    ? route.fulfill({ status: 503 }) : route.fulfill({ json: report }));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.getByText('Model results and limitations (1 approaches)', { exact: true }).click();
  await expect(page.getByText('Brier 0.240 · log loss 0.680')).toBeVisible();
  failed = true;
  await page.getByRole('button', { name: 'Refresh model research', exact: true }).click();
  await expect(page.getByText('Model evidence is unavailable. Retained estimates are hidden until a successful refresh.')).toBeVisible();
  await expect(page.getByText('Brier 0.240 · log loss 0.680')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toBeVisible();
});

test('unapproved status and missing evidence cannot imply model qualification', async ({ page }) => {
  let malformed = false;
  await page.route('**/research-api/v1/research/model-comparison', route => route.fulfill({
    json: malformed ? { ...report, status: 'APPROVED' } : { status: 'INSUFFICIENT EVIDENCE', report: null },
  }));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await expect(page.getByText('INSUFFICIENT EVIDENCE · No completed model comparison is available.')).toBeVisible();
  malformed = true;
  await page.getByRole('button', { name: 'Refresh model research', exact: true }).click();
  await expect(page.getByText('Model evidence is unavailable. Retained estimates are hidden until a successful refresh.')).toBeVisible();
});
