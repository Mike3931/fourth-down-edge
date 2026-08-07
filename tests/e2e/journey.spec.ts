import { expect, test } from '@playwright/test';

/**
 * End-to-end user journey. Run with:
 *   npx playwright install chromium
 *   npm run preview   (in another shell — serves the production build)
 *   npx playwright test
 */

test.describe('Fourth Down Edge core journey', () => {
  test('sign in → slate → game lab → manual price → paper bet → portfolio → settlement → audit', async ({ page }) => {
    await page.goto('/');

    // 1) Sign in with the local demo session.
    await page.getByRole('button', { name: /enter local demo session/i }).click();
    await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS').first()).toBeVisible();

    // 2) Today's Picks (the home page) shows the simple top-picks list.
    await expect(page.getByRole('heading', { name: "Today's Top Picks" })).toBeVisible();
    await expect(page.getByText('Full analysis →').first()).toBeVisible();

    // 3) Weekly slate renders and links into Game Lab.
    // The demonstration slate moved to /slate-demo when /slate was given to
    // the engine-backed screen; this journey exercises the demo flow.
    await page.goto('/slate-demo');
    await expect(page.getByText(/Weekly slate — /)).toBeVisible();
    await page.locator('tbody tr td:nth-child(2) a').first().click();
    await expect(page.getByText('Expected margin (home)')).toBeVisible();
    await expect(page.getByText('Prediction vintages')).toBeVisible();
    await expect(page.getByText('EXPLORATORY — NOT PRODUCTION')).toBeVisible();

    // 4) Market Monitor: enter and confirm a manual price.
    await page.getByRole('link', { name: 'Market Monitor' }).click();
    await page.getByLabel('Game').selectOption({ index: 1 });
    await page.getByLabel('American odds').fill('-105');
    await page.getByLabel(/Line \(blank/).fill('-2.5');
    await page.getByLabel(/Observed how many minutes ago/).fill('1');
    await page.getByText('I confirm this price is currently visible').click();
    await page.getByRole('button', { name: /record immutable price/i }).click();
    await expect(page.getByText(/Recorded immutable price/)).toBeVisible();

    // 5) Portfolio shows the seeded ledger; settle the open demo bet.
    await page.getByRole('link', { name: 'Bet Portfolio' }).click();
    await expect(page.getByText('Bet ledger — append-only')).toBeVisible();
    const settleButton = page.getByRole('button', { name: 'Settle' }).first();
    if (await settleButton.isVisible()) {
      await settleButton.click();
      await page.getByLabel('Settlement result').selectOption('WIN');
      await page.getByRole('button', { name: 'OK' }).click();
    }
    await page.getByRole('tab', { name: /SETTLED/ }).click();
    await expect(page.getByText('WIN').first()).toBeVisible();

    // 6) Performance Lab and Model Audit reproduce linked metadata.
    await page.getByRole('link', { name: 'Performance Lab' }).click();
    await expect(page.getByLabel('Primary metrics').getByText('Log loss')).toBeVisible();
    await expect(page.getByText('Reliability diagram — predicted vs observed')).toBeVisible();

    // Model Audit and Data Health now serve engine data at their old
    // paths; the demonstration versions moved alongside the demo slate.
    await page.goto('/models-demo');
    await expect(page.getByText('fde-ensemble').first()).toBeVisible();
    await expect(page.getByText('PLACEHOLDER').first()).toBeVisible();

    // 7) Data Health lists feeds and degraded states.
    await page.goto('/health-demo');
    await expect(page.getByText('Feed monitors')).toBeVisible();
    await expect(page.getByText('DATA INCOMPLETE').first()).toBeVisible();
  });
});
