import { expect, test } from '@playwright/test';

const briefing = {
  version: 'war-room-foundation-v1', as_of_at: new Date().toISOString(), timezone: 'America/Chicago',
  today_count: 0, observed_market_games: 0, verified_bet365_count: 0, qualified_count: 0,
  decision: 'NO_BET', briefing: 'No verified bet365 Tennessee opportunity qualifies.',
  latest_run: null, changes: [], games: [],
};

test('War Room is the real-data front door on desktop and mobile', async ({ page }) => {
  await page.route('**/research-api/v1/research/briefing', route => route.fulfill({ json: briefing }));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await expect(page).toHaveURL(/\/war-room$/);
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toBeVisible();
  await expect(page.getByText('No captured games in this window')).toBeVisible();
  await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS')).toHaveCount(0);
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole('button', { name: 'Refresh briefing' })).toBeVisible();
});

test('an outage hides cached findings after refresh', async ({ page }) => {
  let failed = false;
  await page.route('**/research-api/v1/research/briefing', route => failed ? route.fulfill({ status: 503 }) : route.fulfill({ json: briefing }));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toBeVisible();
  failed = true;
  await page.getByRole('button', { name: 'Refresh briefing' }).click();
  await expect(page.getByText('Research is unavailable. No cached findings are presented as current.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'NO BET', exact: true })).toHaveCount(0);
});

test('malformed or falsely qualified response fails closed', async ({ page }) => {
  await page.route('**/research-api/v1/research/briefing', route => route.fulfill({ json: { ...briefing, qualified_count: 1 } }));
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
  await expect(page.getByText('Research is unavailable. No cached findings are presented as current.')).toBeVisible();
});
