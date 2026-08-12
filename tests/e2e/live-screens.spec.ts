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

  test('Settings does not promise a mode the ledger refuses', async ({ page }) => {
    // `placeBet` rejects any mode that is not exactly PAPER — pinned by
    // packages/calculations/tests/paper-mode-mandatory.test.ts — and both
    // UI call sites pass the literal 'PAPER'. `settings.mode` never
    // reaches the ledger at all; it drives two badges.
    //
    // So "Real Tracking Mode only records wagers you have already placed
    // manually elsewhere" described something the build does not do, and
    // a user who enabled it saw REAL TRACKING MODE in the header while
    // every wager was still written as paper, indistinguishably.
    await page.goto('/settings');
    await expect(page.getByText(/every wager .* recorded as paper/i).first()).toBeVisible();

    // And the mode section must not claim the toggle changes the record.
    await expect(
      page.getByText('Real Tracking Mode only records wagers you have already placed'),
    ).toHaveCount(0);
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

test.describe('a stored line always says whose it is', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /enter local demo session/i }).click();
  });

  test('CLV is never labelled in bare points', async ({ page }) => {
    // `closingLineValuePct` is PROBABILITY points. Both screens printed it
    // as "+0.45 pts", which in this domain reads as LINE points — a much
    // larger claim from the same number — while Performance Lab's own
    // chart caption said "probability points" about that same 0.45.
    for (const path of ['/performance', '/portfolio']) {
      await page.goto(path);
      await expect(page.getByText(/CLV[^\n]*\b\d+\.\d+ pts\b/)).toHaveCount(0);
      await expect(page.getByText(/CLV \((probability points|prob pts)\)/).first()).toBeVisible();
    }
  });

  test('the odds board names the team on every spread', async ({ page }) => {
    // Spreads are stored HOME-relative and these rows are labelled
    // away-first ("CIN @ LAR"), so a bare "+9" in a column headed "Open
    // spread" reads as the away team's number when it is the home team's.
    // Same defect the Live Slate consensus card was fixed for; this screen
    // still had it.
    await page.goto('/market');
    const firstSpread = page.locator('tbody tr').first().locator('td').nth(1);
    await expect(firstSpread).toHaveText(/^[A-Z]{2,3} [+-]/);
  });
});

test.describe('the two screens Phase 3 named', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /enter local demo session/i }).click();
  });

  // These two originally asserted copy that only exists once data has
  // loaded, so they passed locally with the engine running and failed in
  // CI where it is not — the mirror image of the mistake this file's own
  // header warns about, committed while quoting it. Both now assert the
  // invariant across BOTH states, like the screens above.

  test('Candidates never presents itself as a wager', async ({ page }) => {
    await page.goto('/candidates');

    // Holds unconditionally: neither the loaded screen nor the
    // engine-down screen may carry a status badge reading BET. Exact
    // match, so the disclaimer sentence ("no BET state exists in it") is
    // not caught while a badge would be. An earlier version used a
    // word-boundary regex built through a Python string, and the escape
    // became a literal control character — the pattern matched nothing
    // and the test passed while asserting nothing at all.
    await expect(page.getByText('BET', { exact: true })).toHaveCount(0);

    const down = page.getByText('The analytical engine is not reachable.');
    const disclaimed = page.getByText(/not a wager/i).first();
    await expect(down.or(disclaimed).first()).toBeVisible();
  });

  test('Model Audit does not call a burned season out-of-sample', async ({ page }) => {
    // docs/model-governance.md, binding: "Neither 2024 nor 2025 may be
    // presented as an out-of-sample test result for any model developed or
    // selected after 2026-08-01." The footer said "Out-of-sample backtest
    // scores" while Forward Test called the same seasons burned in its own
    // header, so the two screens disagreed and the generous reading sat
    // beside the numbers.
    //
    // Asserted as an absence, which holds whether the engine is up or
    // down — the phrase must not exist on this screen in either state.
    await page.goto('/models');
    await expect(page.getByText(/out-of-sample backtest/i)).toHaveCount(0);

    // And no superiority claim, which a burned period cannot support and
    // which the project's constraints forbid outright.
    await expect(page.getByText(/better than the market/i)).toHaveCount(0);
  });

  test('Forward Test says its sample cannot support a claim', async ({ page }) => {
    await page.goto('/forward-test');
    // Whatever the numbers say, the caveat must be present — it is the
    // difference between a record and a track record. When the engine is
    // down there are no numbers to caveat, and saying so is the honest
    // alternative.
    const down = page.getByText('The analytical engine is not reachable.');
    const caveat = page.getByText(/not evidence of profitability/i).first();
    await expect(down.or(caveat).first()).toBeVisible();
  });
});
