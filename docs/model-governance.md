# Model governance

## Rules

1. **A language model never creates final numerical probabilities.** Final predictions come from
   versioned statistical models and deterministic code. Explainability text may summarize structured
   factors but cannot change any number or status.
2. **Every prediction is traceable**: model version id, feature-set version, feature snapshot (content
   hash), calibration version, artifact hash, git commit, and the `as_of_at` cutoff.
3. **Point-in-time discipline**: a prediction may use only records with `observed_at <= as_of_at`.
   Violations throw `LookaheadError` in code and are structurally prevented in SQL.
4. **Vintages are immutable**: OPENING → EARLY_WEEK → PRACTICE_UPDATE → FINAL_INJURY_REPORT →
   PREGAME → CLOSING_CAPTURE are separate rows; UPDATE/DELETE on predictions is forbidden by trigger.
5. **Placeholder policy**: components without validated performance are registered as `PLACEHOLDER`,
   carry ensemble weight 0, and render with an explicit PLACEHOLDER badge everywhere they appear.
   Nothing may present a placeholder as validated.
6. **Approval**: only `APPROVED_*` model versions may produce official predictions; the demo tier is
   `APPROVED_DEMO` and is labeled as not validated for real-money decisions.
7. **Drift**: every model carries a drift status and next-review date surfaced in Model Audit.
8. **Language constraints**: the product never uses "lock", "guaranteed", "sure thing", "can't
   lose", "easy money", or "risk-free", and never claims or implies guaranteed profit.

## Current registry (v1)

| Model | Version | Status | Weight in ensemble |
| --- | --- | --- | --- |
| market-baseline | 1.2.0 | APPROVED_DEMO | 0.45 |
| dynamic-team-rating | 0.9.1 | APPROVED_DEMO | 0.11 |
| regularized-stat-baseline | 0.8.0 | APPROVED_DEMO | 0.11 |
| bayesian-hierarchical | 0.1.0 | **PLACEHOLDER** | 0 |
| gradient-boosting | 0.1.0 | **PLACEHOLDER** | 0 |
| player-availability-adjustment | 0.5.0 | APPROVED_DEMO | 0.11 |
| monte-carlo-simulator | 0.1.0 | **PLACEHOLDER** | 0 |
| calibration-layer | 0.6.0 | APPROVED_DEMO | 0.11 |
| fde-ensemble | 0.3.0-demo | APPROVED_DEMO | — |

The table above is the **demo tier inside the TypeScript app**, operating on
synthetic demonstration data. It is unrelated to the analytical-engine
registry below.

---

# Phase 2 analytical-engine governance

Registry: `apps/api` (`fde_api.registry`). Frozen at tag
`phase-2-research-engine-v1`.

## Registered artifacts

Every artifact below is `research_only`. This status is written from a
single hardcoded constant (`registry.RESEARCH_ONLY`); no API input,
configuration value, environment variable, or URL can change it, and no
code path in the repository sets any other approval status.

| Model version | Approval | Seed | Artifact hash |
| --- | --- | ---: | --- |
| naive-homefield-v1 | `research_only` | 20260801 | `3e19da1e7477` |
| naive-rolling-v1 | `research_only` | 20260801 | `01b46e5a889b` |
| market-benchmark-v1 | `research_only` | 20260801 | `c28ea742a1ca` |
| team-ratings-v1 | `research_only` | 20260801 | `1ad358bf8464` |
| glm-ridge-v1 | `research_only` | 20260801 | `51ea1332f4ed` |
| market-residual-v1 | `research_only` | 20260801 | `7fd7c355acb3` |

## Sequential replay: a latent lookahead, closed

`sequential_ratings_moments` is how the in-season-updating ratings model
gets evaluated on the test season. It walks games in kickoff order:
advance the clock to a kickoff, observe every result already visible,
then predict.

`ReplayClock.can_see` is inclusive (`observed_at <= now`). A result
stamped *exactly at its own kickoff* therefore satisfies it at the moment
that game is being predicted — the model would be updated with the
outcome before forecasting it.

Nothing in the replay prevented that. It was prevented only by
`RESULT_AVAILABILITY_OFFSET = 4h30m`, a constant in
`canonical/load_games.py`. The replay silently depended on a value in
another module, and a change to that constant, or results loaded from a
source that stamps them differently, would have leaked without any error.

This matters more than most latent bugs because of how it fails: no crash,
no implausible number. It would simply raise the ratings model's measured
skill on the test season — the single figure the walk-forward exists to
produce, and the one that is only measured once.

The replay now validates the invariant itself and raises `LookaheadError`
naming the offending game, and additionally never observes the game it is
about to predict. Covered by `apps/api/tests/test_sequential_replay_leak.py`,
whose decisive test flips a game's own result and asserts its own
prediction does not move — paired with a control that flips the same
result and asserts a *later* prediction does move, so the test cannot pass
by the model simply ignoring everything.

**No results are invalidated.** The loader has always stamped +4h30m, so
the leak was never realised in any run that produced the recorded numbers.

## Burned evaluation periods — binding constraint

**Seasons 2024 and 2025 have been inspected and are no longer untouched
test sets.** Their results were read, compared across six models, and
reported. Any future model-selection decision that consults those
numbers — even indirectly, by choosing a direction because of what they
showed — is selection on seen data.

Consequences, binding from this tag forward:

1. Neither 2024 nor 2025 may be presented as an out-of-sample test result
   for any model developed or selected after 2026-08-01.
2. Reusing them for tuning, feature selection, threshold setting,
   calibration choice, or ensemble weighting produces an optimistically
   biased estimate that must not be reported as clean.
3. A model developed after this date needs a genuinely untouched period —
   the 2026 season, or a forward-collected holdout — to support any
   out-of-sample claim.
4. Re-running the existing pipeline unchanged for reproducibility is
   fine. Iterating against these seasons and reporting the improvement as
   out-of-sample is not.

What was inspected: log loss, Brier, CRPS (margin and total), margin and
total MAE, calibration intercept/slope, reliability bins, and the
simulated-betting ledger for test seasons 2024 and 2025 at the PREGAME
horizon, across all six artifacts. Selection inputs (decay half-life,
ridge alpha, ratings `k`, calibration method, edge threshold) were chosen
on the preceding validation season only, so the 2024/2025 figures were
honest when produced — they are burned by the act of reading them, not by
how they were generated.

## Real-money controls (verified at freeze)

The analytical engine has **no `BET` state**. Its only statuses are
`RESEARCH_CANDIDATE`, `WATCH`, `PASS`, `DATA_INCOMPLETE`; the string
`BET` does not appear anywhere in `apps/api/src`. Verified at freeze:

* Approval status is written in exactly one place, as a constant.
* No endpoint accepts or mutates approval status; API references are
  read-only serialization.
* `services/generate.py` refuses to serve any status outside
  `research_only` / `approved`, and nothing in the repository produces
  `approved`.
* The web app reads no query string or hash fragment; the only route
  parameter is `:gameId`, and an unknown value renders "Unknown game".
* Both bet-recording call sites pass the literal `'PAPER'`.
* Outside the research client, the app makes no `fetch`, `XMLHttpRequest`,
  or `axios` call — it has no capability to transmit a wager anywhere.
* The research path never constructs a `Recommendation` and never calls
  `placePaperBet`; it is display-only.

### Open finding (not corrected during the freeze)

`REAL_TRACKING` mode can be enabled through the gated Settings flow, but
both bet-recording call sites hardcode `'PAPER'`, so ledger entries are
recorded as `PAPER` regardless of mode. The mode therefore changes only
the header pill and the Settings copy.

This is **fail-safe** — it errs toward paper and cannot produce a
real-money-labeled record — so it was deliberately left unchanged at this
checkpoint rather than "fixed" in a direction that would widen the
real-money surface. It is recorded here for a deliberate product decision
in a later phase: either wire the mode through to the recording path, or
remove the mode and its Settings flow.
