import { expect, test } from '@playwright/test';

/**
 * Separate, fail-closed acceptance gate. Unlike live-screens.spec.ts,
 * engine-down is NEVER acceptable in this test suite. The CI job must
 * start an actual FastAPI process against a migrated test database.
 *
 * An empty test slate is acceptable: this checks connectivity/provenance,
 * not an assertion that licensed NFL prices or real predictions exist.
 */
test('the real backend responds with a migrated database', async ({ request }) => {
  const health = await request.get('/research-api/health');
  expect(health.ok(), `backend /health returned ${health.status()}`).toBeTruthy();
  const body = await health.json();
  expect(body.database).toBe('ok');
  expect(body.auth).not.toMatch(/misconfigured/i);
  expect(body.research_banner).toMatch(/RESEARCH/i);

  const slate = await request.get('/research-api/v1/forward/slate');
  expect(slate.ok(), `forward slate returned ${slate.status()}`).toBeTruthy();
  const result = await slate.json();
  expect(result.data_mode).toBe('LIVE_RESEARCH');
  expect(result.count).toBe(result.games.length);
  expect(Array.isArray(result.games)).toBeTruthy();
});

test('the live slate uses the real engine rather than the demo fallback', async ({ page }) => {
  const response = page.waitForResponse((r) => r.url().endsWith('/research-api/v1/forward/live'));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  expect((await response).ok()).toBeTruthy();
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.getByRole('heading', { name: 'Live Slate' })).toBeVisible();
  await expect(page.getByText('REAL CAPTURED DATA', { exact: true })).toBeVisible();
  await expect(page.getByText('The analytical engine is not reachable.')).toHaveCount(0);
  await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS')).toHaveCount(0);
});

test('the live schedule comes from the backend, even when no games are seeded', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.goto('/slate');
  await expect(page.getByRole('heading', { name: 'Slate' })).toBeVisible();
  await expect(page.getByText('LIVE FROM ENGINE', { exact: true })).toBeVisible();
  await expect(page.getByText('The analytical engine is not reachable.')).toHaveCount(0);
});

test('data-health endpoint is reachable and its UI is not a demo', async ({ page, request }) => {
  const health = await request.get('/research-api/v1/forward/health');
  expect(health.ok(), `forward health returned ${health.status()}`).toBeTruthy();
  const data = await health.json();
  expect(data.data_mode).toBe('LIVE_RESEARCH');
  expect(Array.isArray(Object.values(data.by_scope).flat())).toBeTruthy();

  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await page.goto('/health');
  await expect(page.getByRole('heading', { name: 'Data Health' })).toBeVisible();
  await expect(page.getByText('LIVE FROM ENGINE', { exact: true })).toBeVisible();
  await expect(page.getByText('The analytical engine is not reachable.')).toHaveCount(0);
});

test('research candidate and captured-market API endpoints fail closed, not mock-backed', async ({ request }) => {
  for (const endpoint of ['/research-api/v1/forward/live', '/research-api/v1/forward/candidates', '/research-api/v1/forward/performance']) {
    const result = await request.get(endpoint);
    expect(result.ok(), `${endpoint} returned ${result.status()}`).toBeTruthy();
    const payload = await result.json();
    expect(payload.data_mode, `mode for ${endpoint}`).toBe('LIVE_RESEARCH');
    expect(payload.research_banner).toMatch(/RESEARCH/i);
  }
});
