import { expect, test, type Page } from '@playwright/test';

/**
 * UI contract tests, with explicit fixed fixtures. These DO NOT count as
 * live provider validation: the separate live-engine-required suite asserts
 * the actual FastAPI process is reachable. These test that UI rendering is
 * truthful, links use canonical IDs, and no demo pick is substituted.
 */
async function stubCapturedGame(page: Page, quotes: unknown[] = []) {
  const kickoff = new Date(Date.now() + 24 * 60 * 60_000).toISOString();
  const observed = new Date(Date.now() - 2 * 60_000).toISOString();
  await page.route('**/research-api/v1/forward/slate', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      data_mode: 'LIVE_RESEARCH', generated_at_utc: observed, count: 1,
      games: [{ canonical_game_id: 'FDE-REAL-TEST-GAME', away_team_id: 'TEN', home_team_id: 'HOU',
        kickoff_utc: kickoff, season: 2026, season_type: 'REG', week: 7,
        venue: 'Test Venue', neutral_site: false, game_status: 'SCHEDULED', schedule_provider: 'nflverse' }],
    }),
  }));
  await page.route('**/research-api/v1/forward/live', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      data_mode: 'LIVE_RESEARCH', generated_at_utc: observed, not_a_claim: 'Not a price offer.',
      games: [{ canonical_game_id: 'FDE-REAL-TEST-GAME', observed_at: observed,
        schedule_provider: 'nflverse', books: quotes.length ? ['test book'] : [], quotes,
        markets: {
          SPREAD: { consensus: null, reasons: ['Only one eligible book; minimum is 3'], eligible_books_now: quotes.length ? 1 : 0 },
          TOTAL: { consensus: null, reasons: ['No eligible prices'], eligible_books_now: 0 },
          MONEYLINE: { consensus: null, reasons: ['No eligible prices'], eligible_books_now: 0 },
        }, final: null }],
    }),
  }));
  await page.route('**/research-api/v1/forward/candidates', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ data_mode: 'LIVE_RESEARCH', generated_at_utc: observed,
      gate: { open: true, blocked_by: [], degraded_by: [] }, count: 0,
      cohorts_present: [], mixed_cohorts: false, candidates: [], not_a_claim: 'Not a wagering offer.' }),
  }));
}

async function login(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: /enter local demo session/i }).click();
}

test('a real fixture links to an engine-backed Game Lab with no invented price', async ({ page }) => {
  await stubCapturedGame(page);
  await login(page);
  await page.goto('/slate');
  const matchup = page.getByRole('link', { name: 'TEN @ HOU' });
  await expect(matchup).toBeVisible();
  await matchup.click();
  await expect(page).toHaveURL(/\/live\/game\/FDE-REAL-TEST-GAME$/);
  await expect(page.getByRole('heading', { name: /Game Lab · TEN @ HOU/ })).toBeVisible();
  await expect(page.getByText('ENGINE-BACKED RESEARCH')).toBeVisible();
  await expect(page.getByText('DATA INCOMPLETE')).toHaveCount(3);
  await expect(page.getByText('Only one eligible book; minimum is 3')).toBeVisible();
  await expect(page.getByText('No active WATCH or RESEARCH_CANDIDATE entry')).toBeVisible();
  await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS')).toHaveCount(0);
});

test('unknown canonical game never substitutes a fictional demo', async ({ page }) => {
  await stubCapturedGame(page);
  await login(page);
  await page.goto('/live/game/NOT-REAL');
  await expect(page.getByRole('heading', { name: 'Game not found in the live schedule' })).toBeVisible();
  await expect(page.getByText('No demo game was substituted.')).toBeVisible();
  await expect(page.getByText('DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS')).toHaveCount(0);
});

test('a captured quote is described as history, not offered as an available wager', async ({ page }) => {
  await stubCapturedGame(page, [{ sportsbook: 'test book', market: 'SPREAD', selection: 'HOME',
    line: -2.5, american: -110, decimal_odds: 1.91, provider: 'licensed fixture',
    provider_mode: 'SANDBOX', observed_at: new Date(Date.now() - 2 * 60_000).toISOString() }]);
  await login(page);
  await page.goto('/live/game/FDE-REAL-TEST-GAME');
  await expect(page.getByRole('table', { name: 'Latest observed market prices' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'test book', exact: true })).toBeVisible();
  await expect(page.getByText('licensed fixture / SANDBOX')).toBeVisible();
  await expect(page.getByText(/At least one quote is not labelled LIVE by its source/)).toBeVisible();
  await expect(page.getByText('No active WATCH or RESEARCH_CANDIDATE entry')).toBeVisible();
  await expect(page.getByText('Research only. Models have not been approved')).toBeVisible();
});


test('future-dated source observations are explicitly flagged', async ({ page }) => {
  await stubCapturedGame(page, [{ sportsbook: 'test book', market: 'SPREAD', selection: 'HOME',
    line: -2.5, american: -110, provider: 'fixture', provider_mode: 'SANDBOX',
    observed_at: new Date(Date.now() + 30 * 60_000).toISOString() }]);
  await login(page);
  await page.goto('/live/game/FDE-REAL-TEST-GAME');
  await expect(page.getByText(/Unverified or future-dated observation time/)).toBeVisible();
});

test('same-time contradictory sportsbook quotes are not silently accepted', async ({ page }) => {
  const observedAt = new Date(Date.now() - 2 * 60_000).toISOString();
  await stubCapturedGame(page, [
    { sportsbook: 'test book', market: 'SPREAD', selection: 'HOME', line: -2.5,
      american: -110, provider: 'fixture', provider_mode: 'SANDBOX', observed_at: observedAt },
    { sportsbook: 'test book', market: 'SPREAD', selection: 'HOME', line: -3.5,
      american: -120, provider: 'fixture', provider_mode: 'SANDBOX', observed_at: observedAt },
  ]);
  await login(page);
  await page.goto('/live/game/FDE-REAL-TEST-GAME');
  await expect(page.getByText(/Conflicting source observations at the same timestamp/)).toBeVisible();
});


test('Game Lab supports mobile, refresh and accessible navigation', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await stubCapturedGame(page);
  await login(page);
  await page.goto('/live/game/FDE-REAL-TEST-GAME');
  await expect(page.getByRole('heading', { name: /Game Lab · TEN @ HOU/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.reload();
  await expect(page.getByRole('heading', { name: /Game Lab · TEN @ HOU/ })).toBeVisible();
  await page.getByRole('button', { name: 'Refresh analysis' }).click();
  const back = page.getByRole('link', { name: '← All games' });
  await back.focus();
  await back.press('Enter');
  await expect(page).toHaveURL(/\/slate$/);
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
});

test('Game Lab recovers from a schedule failure without a demo fallback', async ({ page }) => {
  await stubCapturedGame(page);
  await page.route('**/research-api/v1/forward/slate', (route) => route.fulfill({ status: 503, body: 'Unavailable' }));
  await login(page);
  await page.goto('/live/game/FDE-REAL-TEST-GAME');
  await expect(page.getByText('The analytical engine is not reachable.')).toBeVisible();
  await page.unroute('**/research-api/v1/forward/slate');
  await stubCapturedGame(page);
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('heading', { name: /Game Lab · TEN @ HOU/ })).toBeVisible();
});

test('equal instants with different UTC offsets retain both conflicting prices', async ({ page }) => {
  const instant = new Date(Date.now() - 120_000);
  const equivalent = new Date(instant.getTime() - 5 * 3600_000).toISOString().replace('Z', '-05:00');
  await stubCapturedGame(page, [
    { sportsbook: 'test book', market: 'SPREAD', selection: 'HOME', line: -2.5, american: -110, provider: 'fixture', provider_mode: 'LIVE', observed_at: instant.toISOString() },
    { sportsbook: 'test book', market: 'SPREAD', selection: 'HOME', line: -3.5, american: -120, provider: 'fixture', provider_mode: 'LIVE', observed_at: equivalent },
  ]);
  await login(page);
  await page.goto('/live/game/FDE-REAL-TEST-GAME');
  await expect(page.getByText(/Conflicting source observations at the same timestamp/)).toBeVisible();
  await expect(page.getByRole('cell', { name: 'test book', exact: true })).toHaveCount(2);
});
