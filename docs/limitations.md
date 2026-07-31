# Known limitations (version 1)

**Read this before using the application for anything.**

1. **Every number is demonstration data.** The slate, odds, injuries, weather, predictions,
   backtest, and performance metrics are generated deterministically from a seeded PRNG and labeled
   "DEMONSTRATION DATA — NOT FOR REAL-MONEY DECISIONS" on every screen. None of it describes real
   NFL games or a real model.
2. **No validated predictive model exists yet.** The "ensemble" is a demo scaffold; Bayesian,
   gradient-boosting, and Monte Carlo components are registered as PLACEHOLDER with weight 0. No
   claim of profitability is made anywhere, and none should be inferred.
3. **Demo thresholds.** The 2.0% minimum edge, completeness minimums, and haircut multipliers are
   demo values for exercising the workflow — not historically validated.
4. **Player names are fictional.** Real team names appear as plain text only.
5. **Mock consensus market.** One mocked consensus book; median/best-price across books and closing-
   line capture activate with a real odds provider.
6. **The analytical service is not built.** v1 computes demo predictions in the browser behind the
   typed API seam. Proprietary model logic must move to the Python service (see docs/roadmap.md).
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
    deployments use wall-clock time.
12. **Bundle size**: the build is split into app (~233 kB), vendor (~251 kB), and charts (~416 kB)
    chunks. Charts remain the largest dependency; route-level lazy loading would trim first paint
    further if that ever matters.
