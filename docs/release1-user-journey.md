# Fourth Down Edge — Release 1 / Real-Game Lab incremental delivery

**Source:** `feature/forward-data-capture` at `f388e6954ab886ab11990272d8d10cc344b9e52c`.

## What this patch implements

1. Adds `LiveGameLab.tsx`: a new, **read-only** real-data game detail view at
   `/live/game/:gameId`, using the backend's canonical slate, observed book
   quotes, stored consensus and frozen research candidates.
2. Distinguishes sandbox/non-live quote provenance, stale/future timestamps, and same-time contradictory quotes; none can be mistaken for a current executable book offer.
3. Links real matchup rows from the live Slate, Live Slate and Candidates to
   that real Game Lab. Never routes a real ID into the synthetic `/game/:gameId`
   demonstration experience.
4. Marks the new detail route as *live*, removing the erroneous red demo-data
   banner and frozen-clock header; explicitly labels the old Game Lab as demo.
5. Clarifies in Settings that its editable URL is for the legacy Game Lab,
   while live screens use the bundled `ENGINE_BASE` configuration.
6. Adds separate browser UX tests with **clearly mocked research fixtures**.
   These verify navigation, missing markets and no fake fallback, but are
   not used as evidence that a licensed odds feed works.

## Deliberate limits

- **No real-money bets.** Models are still `research_only`.
- **No live paper bet workflow yet.** This is the safe first half of the user
  journey (real slate → real Game Lab), not a complete bet tracker.
- **No bet365 connection**, scraping, automated polling or automatic wagers.
- Market snapshots are **captured observations**, not currently executable
  book prices. A one-book burn-in remains explicitly caveated.
- Research candidates are limited to the backend's most recent forward-ledger
  entries within its fixed horizon; an absent candidate is not an algorithmic
  PASS verdict.
- Model-performance claims, bankroll history and demonstrations remain
  separated; nothing in this change turns research-only artifacts into an
  approved strategy.
- The existing API returns all quotes over the fixed horizon, so further
  scalability work is needed for high-frequency production feeds.

## Acceptance checks

- `npm run build` compiles the page and route (CI required).
- `npx playwright test` exercises fixture-backed UI tests in addition to
  existing demo tests (CI required).
- `npx playwright test --config=playwright.live.config.ts` requires an
  actual FastAPI service and fresh isolated migrated test DB (CI required).
- Real slate/candidate links must land on the same canonical ID.
- Zero demo values, automatic bets, or undisclosed synthetic calculations.
- Missing quote and missing candidate states must be visibly distinct.

## Next implementation after this slice

- Add a versioned, user-owned, append-only **real-fixture paper ledger** with
  full foreign-key references to canonical game, frozen prediction, manually
  entered price, and recorded quote time. Test idempotent settlement with
  verified results; no silent price or model changes after entry.
- Implement backend-backed injury and data-quality panels. Add an approved,
  licensed three-or-more-book market feed before claiming official consensus.
- Build an actual user-friendly home screen based on current games and data
  readiness; move diagnostic and legacy demo pages to secondary navigation.
- Require prospective calibration + benchmark evidence before permitting
  any real-money interpretation of candidate grades or proposed stake.

## Product-fit warning

This remains a technical increment, not the daily-use product the user asked
for. See `docs/product-outcome-gap.md`: a single real NFL journey with a
manually entered line, append-only paper ledger and transparent uncertainty is
still missing. Same-game-parlay evaluation is explicitly outside this slice;
no joint correlated props model exists, so naive independent-leg calculations
must not be introduced merely to satisfy a request for parlays.
