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
  { path: '/candidates', heading: 'Research Candidates' },
  { path: '/forward-test', heading: 'Forward Test' },
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
    // Exact, so the nav's grouping heading is not confused with the
    // header's per-screen state line, which also says "data".
    await expect(page.getByText('Live engine data', { exact: true })).toBeVisible();
    await expect(page.getByText('Demonstration data', { exact: true })).toBeVisible();
  });
});

test.describe('screens that show no dataset at all', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /enter local demo session/i }).click();
  });

  test('Settings carries neither live nor demonstration chrome', async ({ page }) => {
    // Settings holds paper mode, the hard monthly loss budget and the
    // bankroll caps, and reads no dataset. The nav stopped filing it under
    // "Demonstration data" because that told the reader those controls
    // were part of the demo — but the header went on stamping
    // DEMONSTRATION DATA directly above them, which is the same claim by
    // another route.
    await page.goto('/settings');

    await expect(
      page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS'),
    ).toHaveCount(0);
    await expect(page.getByText(/Real captured data/)).toHaveCount(0);
    await expect(page.getByText(/These control real behaviour/)).toBeVisible();

    // The controls it exists for are still there and still say what they are.
    await expect(page.getByText('PAPER MODE').first()).toBeVisible();
  });

  test('the demonstration screens still carry their banner', async ({ page }) => {
    // The guard on the fix above: quieting the banner must not have
    // quieted it where it belongs.
    await page.goto('/picks-demo');
    await expect(
      page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS').first(),
    ).toBeVisible();
  });
});

test.describe('the two screens Phase 3 named', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /enter local demo session/i }).click();
  });

  test('Candidates never presents itself as a wager', async ({ page }) => {
    await page.goto('/candidates');
    // The engine has no BET state and this screen must not invent one.
    // Exact match, so a STATUS BADGE reading "BET" is caught while the
    // disclaimer sentence ("no BET state exists in it") is not. The first
    // version used a word-boundary regex built through a Python string, and
    // the escape became a literal control character: the pattern matched
    // nothing, so the test passed while asserting nothing at all.
    await expect(page.getByText('BET', { exact: true })).toHaveCount(0);
    await expect(page.getByText(/not a wager/i).first()).toBeVisible();
  });

  test('Forward Test says its sample cannot support a claim', async ({ page }) => {
    await page.goto('/forward-test');
    // Whatever the numbers say, the caveat must be present. It is the
    // difference between a record and a track record.
    await expect(page.getByText(/not evidence of profitability/i).first()).toBeVisible();
  });
});
