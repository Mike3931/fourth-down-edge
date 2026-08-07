import { expect, test } from '@playwright/test';

/**
 * The engine-backed screens must never show demonstration data.
 *
 * Asserted as an invariant rather than as a state, because whether the
 * engine happens to be running differs between a developer machine and CI.
 * Either outcome is acceptable; the third one is not:
 *
 *   engine up    -> real data, badged LIVE FROM ENGINE / REAL CAPTURED DATA
 *   engine down  -> says so plainly, renders nothing
 *   either way   -> never the demonstration generator
 *
 * A screen that quietly swaps real numbers for invented ones is the single
 * failure this separation exists to prevent, and it is invisible in a
 * screenshot. A test that only passed when the engine was down would have
 * given no coverage at all on a machine where it was up.
 */

const LIVE_SCREENS = [
  { path: '/live', heading: 'Live Slate' },
  { path: '/slate', heading: 'Slate' },
  { path: '/models', heading: 'Model Audit' },
  { path: '/health', heading: 'Data Health' },
];

// Strings that only ever appear on the demonstration screens.
const DEMO_ONLY = ['fde-ensemble', 'Feed monitors', 'Weekly slate —'];

test.describe('engine-backed screens', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /enter local demo session/i }).click();
  });

  for (const screen of LIVE_SCREENS) {
    test(`${screen.heading} shows engine data or says the engine is down`, async ({ page }) => {
      await page.goto(screen.path);
      await expect(page.getByRole('heading', { name: screen.heading })).toBeVisible();

      const down = page.getByText('The analytical engine is not reachable.');
      const live = page.getByText(/LIVE FROM ENGINE|REAL CAPTURED DATA/);
      await expect(down.or(live).first()).toBeVisible();

      // The part that matters either way.
      for (const marker of DEMO_ONLY) {
        await expect(page.getByText(marker, { exact: false })).toHaveCount(0);
      }
    });
  }

  test('the navigation separates live screens from demonstration screens', async ({ page }) => {
    await expect(page.getByText('Live engine data')).toBeVisible();
    await expect(page.getByText('Demonstration data', { exact: true })).toBeVisible();
  });
});
