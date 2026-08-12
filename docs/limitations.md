# Known limitations (version 1)

**Read this before using the application for anything.**

1. **Some numbers are demonstration data and some are real captured data, and the app says which
   on every screen.** This item used to read "Every number is demonstration data", which stopped
   being true when the analytical engine started capturing (see item 6) and would now lead a reader
   to dismiss real book prices as generated — a claim about provenance, which is the thing this
   project is most careful about everywhere else.

   The split, as the navigation groups it:

   * **Live engine data** — Live Slate, Slate, Research Candidates, Forward Test, Model Audit,
     Data Health. Real fixtures, real captured book prices, real backtest scores, badged
     `REAL CAPTURED DATA` or `LIVE FROM ENGINE`. When the engine is unreachable these screens say
     so and render nothing; demo numbers are never substituted.
   * **Demonstration data** — Today's Picks, Game Lab, Injury Center, Market Monitor, Bet
     Portfolio, Performance Lab, Weekly Slate. Generated deterministically from a seeded PRNG and
     labeled `DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS`. Player names are fictional and
     no number describes a real NFL game.
   * **Neither** — Settings, which reads no dataset.

   What has not changed: no number anywhere is a betting recommendation, and the engine has no
   `BET` state at all. See items 2 and 3.
2. **No validated predictive model exists yet.** The "ensemble" is a demo scaffold; Bayesian,
   gradient-boosting, and Monte Carlo components are registered as PLACEHOLDER with weight 0. No
   claim of profitability is made anywhere, and none should be inferred.
3. **Demo thresholds.** The 2.0% minimum edge, completeness minimums, and haircut multipliers are
   demo values for exercising the workflow — not historically validated.
4. **Player names are fictional.** Real team names appear as plain text only.
5. **Mock consensus market.** One mocked consensus book; median/best-price across books and closing-
   line capture activate with a real odds provider.
6. **The analytical service IS built, and the browser path still exists beside it.** This item used
   to read "The analytical service is not built."

   `apps/api` is a FastAPI service with a canonical data model, point-in-time guards, six registered
   models, walk-forward backtesting, a frozen forward-test policy and an idempotent scheduler. It
   backs the six live screens in item 1. Every model artifact is registered `research_only` and no
   approval path is implemented.

   What has not moved: the DEMO screens still compute their predictions in the browser, from the
   seeded generator, behind the same typed API seam. Those are two different code paths producing
   two different kinds of number, which is why they are labelled separately and never merged on one
   screen — see `apps/web/src/pages/ForwardTestLive.tsx`, which explains at length why its cohort is
   kept apart from Model Audit's.
7. **Local Demo Mode persistence** is localStorage: single-device, cleared with browser data. The
   Supabase path exists but requires a configured project.
8. **E2E tests require a browser install** (`npx playwright install chromium`). Run with
   `npm run test:e2e`. The full journey (sign in → slate → Game Lab → manual price → paper bet →
   settlement → Performance Lab → Model Audit → Data Health) has been executed against the production
   build and passes.
9. **Push probabilities for spreads/totals** come from a discretized-normal-with-key-number-boost
   approximation — an explicitly labeled placeholder for simulation-based estimates.
10. **Odds feed staleness** uses fixed demo thresholds (30 min aging / 120 min stale) rather than
    market-hours-aware logic.
11. **The frozen demo clock** (2026-09-10T16:00:00Z) keeps freshness states deterministic; real
    deployments use wall-clock time. It also doubles as the live prediction cutoff enforced by
    `evaluateGame` — see item 17.
12. **Bundle size**: the build is split into app (~286 kB), vendor (~251 kB), and charts (~416 kB)
    chunks. Charts remain the largest dependency; route-level lazy loading would trim first paint
    further if that ever matters.
13. **Schedule conflict detection is structurally inert in v1.** The recommendation engine's
    `kickoffTimeConsistent` and `venueConsistent` DATA INCOMPLETE checks always evaluate `true`
    because both this demo dataset and the Supabase schema model exactly one canonical schedule
    source per game — there is nothing for a single source to conflict with. A genuine version of
    this check requires ingesting multiple real schedule providers and reconciling them through
    `entity_mappings`/`source_records`, which is real ingestion work, not a wiring bug. Flagged here
    rather than silently left to look like a live check (see `packages/api-client/src/evaluate.ts`).
14. **Correlated-cluster exposure is a same-game proxy, not a real correlation model.** The staking
    pipeline's cluster cap (1.25% of bankroll) is checked against real portfolio state, but "cluster"
    is defined as same-game, cross-market exposure — the one correlation signal already computed
    elsewhere in the app (the Bet Card's correlation warning) — rather than a genuine cross-game
    correlation model (shared officiating crews, shared weather systems, division/conference
    linkage). The spec does not define how a cluster should be computed; building a real one is
    future analytical work, not a bug fix.
15. **The demo dataset cannot be loaded as-is into the real Supabase schema — by design, not by bug.**
    Verified directly: every real field the browser-side generator produces (588 rows across 13
    tables — teams, stadiums, officials, players, games, game_officials, model_versions,
    injury_reports, weather_snapshots, player_availability_snapshots, odds_snapshots,
    manual_book_prices, predictions, prediction_components, bankroll_accounts, bets) inserts
    cleanly into the actual migrated schema with zero type or constraint errors, once three
    intentional differences are accounted for: (a) UUID-keyed tables let Postgres generate the
    primary key rather than reusing the mock's readable string id, matching how a real ingestion
    pipeline would insert; (b) `bets.recommendation_id` and `bets.feature_snapshot_id` are left
    unset because recommendations are computed live and never persisted (see below), and
    `game_feature_snapshots` belongs to a real ingestion feature-store the client-side generator
    has no reason to populate; (c) `predictions.as_of_at` values use the demo dataset's frozen
    "current" clock (chosen to be a plausible near-future NFL week for narrative purposes), which
    is later than real wall-clock time — the `predictions_asof_guard` trigger correctly rejects
    that for a real insert, exactly as it's supposed to.
16. **Recommendation invalidation over time is not implemented.** `priceWorseThanMaxAcceptable` and
    `invalidatedByNewerInformation` always evaluate `false`: both require comparing current
    conditions against a *persisted* snapshot of the recommendation as it existed earlier, but
    `evaluateGame` recomputes fresh on every call — unlike predictions (which have immutable
    vintages), recommendations are never persisted, so there is nothing earlier to compare against.
    A real fix needs a `recommendations` table write path with history, not just a wiring change
    (the Supabase schema already has the columns for this — `invalidated_at`, `invalidation_reason`
    — the mock layer just never populates them from a genuine before/after comparison).
    `marketMovingTowardAcceptablePrice`, by contrast, **is** wired for real in this audit pass: for
    SPREAD/TOTAL it compares the point line between the opening and current snapshot (the same
    movement Market Monitor already displays — this dataset's per-snapshot American-odds juice is
    drawn independently at random, so a probability computed from juice alone would mostly be
    noise); MONEYLINE falls back to no-vig probability, though this generator computes a single
    moneyline price once per game and reuses it for every snapshot, so moneyline movement will
    never be detected until that's also modeled.
17. **Point-in-time enforcement was found unwired during audit, then fixed and is now live.** The
    guard functions in `@fde/calculations/pointInTime` (`assertNoLookahead`, `filterToCutoff`,
    `isModelUsableAtCutoff`, etc.) — implementing the spec's "future information must never enter a
    historical prediction" requirement — were fully unit-tested in isolation but never called by
    `evaluateGame`, which just used "whatever is latest" with no cutoff check. Fixed:
    `evaluateGame`/`evaluateCandidates` now take the demo clock as an explicit prediction cutoff and
    filter every odds snapshot, injury report, weather snapshot, availability snapshot, and manual
    price to what was actually observable by then; the model-approval check now also verifies the
    approval date, not just the status field. A detected violation degrades that one game to DATA
    INCOMPLETE (consistent with "missing critical information must produce Data Incomplete, not a
    fabricated recommendation") rather than throwing and taking the whole batch evaluation down —
    verified with tests that deliberately corrupt a record's timestamp and confirm both the graceful
    degradation and that it doesn't affect other games (`packages/api-client/tests/lookahead.test.ts`).
    Because the demo dataset was already generated correctly, this defect never manifested in
    practice — which is exactly why nothing before this audit had caught it.

    **Follow-up: the guard is now structural, not conventional.** The fix above left the cutoff as
    an *optional* parameter on `latestSnapshot` and `evaluateCandidates`, with a comment noting
    that the evaluation call sites all happened to pass it. That is the same shape as the original
    defect: correct by inspection, and invisible when wrong, because the dataset still contains
    zero records dated after `demoNow` (asserted in `tests/cutoff-required.test.ts`).

    `latestSnapshot` is now split into `latestSnapshotAsOf` (cutoff required — use anywhere the
    result feeds a prediction or edge) and `latestSnapshotForDisplay` (explicitly unrestricted —
    Weekly Slate, Market Monitor, Game Lab headers, which legitimately show where the market is
    *now*). `evaluateCandidates` requires the cutoff, and the one display caller that omitted it
    now passes `ds.demoNow`.

    This changed no behaviour — with no future-dated records, filtering is a no-op — which is
    precisely the point: the guarantee moved out of the dataset's good behaviour and into the
    signatures. Verified by mutation: reverting the evaluation path to the unrestricted variant
    makes `cutoff-required.test.ts` fail.
