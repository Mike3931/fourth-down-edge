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

Where consequence 1 is enforced: the Model Audit screen described these
same scores as "Out-of-sample backtest scores" in its footer, and rendered
any model beating the benchmark by more than the noise band as "better
than the market" in success green — a superiority claim over a burned
period, which is a stronger claim than the one this section forbids. Both
are gone. `describeBrierDelta` in `@fde/calculations` now states the
measurement ("lower Brier than the market on these games") without the
inference, under test, and `tests/e2e/live-screens.spec.ts` asserts the
absence of both phrases whether the engine is up or down.

Worth recording because of how it survived: the Forward Test screen has
always described these seasons as burned — it explains its own separate
existence by it — so the two screens disagreed about the same data, and
the generous description was the one sitting beside the numbers.

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

**Update — the fail-safe is now enforced, not merely observed.** As
originally written, the guarantee rested entirely on both call sites
happening to pass the literal `'PAPER'`, a fact verifiable only by
reading the code. A third call site, or one edit to an existing one,
would have silently recorded a bet marked `REAL_TRACKING`.

`placeBet` in `packages/calculations/src/ledger.ts` now rejects any mode
other than `PAPER` before touching the ledger, and
`tests/paper-mode-mandatory.test.ts` pins the behaviour — including that
the mode is checked *before* the stake, so a real-money attempt can never
be reported as a mere staking error.

This does not resolve the product question above; it only converts the
current behaviour from convention into construction. Whichever way that
question is decided, it should be decided by changing this guard
deliberately.

## Model-layer audit (2026-08-09)

A read of `models_ml/`, `features/`, `calibration.py`, and `backtest/`
looking for the class of error that had already cost a day elsewhere in
the codebase: a convention held only in the author's head, or a number
verified against a fixture that encoded the answer it was checking.

### Verified sound

**The spread sign convention, against outcomes rather than a fixture.**
`MarketResidual.predict` computes `mu_margin = -market.home_line`, so an
inverted convention here would flip every prediction while remaining
entirely plausible on screen. Checked against 4,454 real games with both
a closing spread and a result: mean of `margin + home_line` is **-0.011**
(inverted would be +3.45), correlation of `-home_line` with actual home
margin is **+0.45**, and the largest home favourites at -22 won by ~25.
The convention holds and the market is close to unbiased.

**Three separate layers agree on it.** `OddsSnapshot.line`, the quotes
the engine captures, and everything in `backtest/execution.py` are all
home-relative. `settle` scores HOME as `margin + line` and AWAY as its
exact negation; `_worsen_line` moves the home line by -0.5 for HOME and
+0.5 for AWAY; `simulate_bets` passes `ref.home_line` for both sides.
Consistent throughout. (`Recommendation.line` in the TypeScript layer is
the one *selection*-relative field; that boundary is documented in
`packages/shared-types` and pinned by
`packages/api-client/tests/spread-convention.test.ts`.)

**Walk-forward split discipline.** Feature half-life, ridge alpha,
ratings k, calibration method, and the edge threshold are all selected on
season N-1 and the fold is tested once on season N. Nothing selects on
test.

**Point-in-time discipline in the feature layer.** Every accessor in
`LeagueHistory` filters on `observed_at` — the result-availability
instant, not kickoff — and excludes the game being predicted.
`_decayed` raises `LookaheadError` on a negative age rather than
weighting a future game at less than one. Rows are sorted by kickoff at
load, so "the most recent prior game" really is that.

**The reported CRPS is exact.** `crps_normal` appears in every report as
`crps_margin` and `crps_total`. Checked against numerical integration of
the CRPS definition — ∫(F(x) − 1{x ≥ y})² dx — rather than against the
same closed form written a second time, over nine cases including
symmetric, far-tail and small-sigma ones: worst relative error 4.2e-16,
i.e. exact to machine precision.

**The block bootstrap is the right shape.** `week_block_bootstrap`
resamples whole weeks with replacement, which is what within-week
correlation requires, and takes a 90% interval from the 0.05/0.95
quantiles. Correct — the issue is only that it is not applied to Brier
(see below).

### Corrected

**Calibration was selected in-sample.** Each candidate was fitted on the
validation sample and then scored on that same sample, which does not
compare calibrators — it ranks them by parameter count. `none` has none,
platt two, beta three, isotonic ~100 knots. On perfectly-calibrated input
the identity method could essentially never win, and on separable input
platt scored a log loss of exactly 0.0 and was reported as the best
available. Measured on held-out data the ordering inverted: at n=1600 the
in-sample winner was the **worst** of four, 0.016 of log loss worse than
leaving the probabilities untouched.

Selection is now by out-of-fold log loss over a seeded 5-fold partition,
with the winner refitted on the whole sample. Isotonic is offered only
when every *training* fold clears `MIN_ISOTONIC_N`, not merely the full
sample.

*Consequence for the published evaluation — now regenerated.* The stored
reports recorded `cal_beta_val2023` and `cal_beta_val2024`. Both folds
were replayed from their own stored configs on 2026-08-10 and both now
select `none`, by about 0.002 of log loss, consistently. Model Brier, log
loss, CRPS and MAE are unchanged to 1e-13 — calibration is applied only
to spread-cover probabilities inside the betting simulation, never to the
metrics the model comparison ranks on.

The betting simulation changed a great deal, and downward: net simulated
P&L across the two test seasons went from −0.21 units to **−6.59**, with
2025 at −17.63% per bet over 47 bets. The old figures largely recorded
suppression — four bets in 2024 is a calibrator shrinking nearly every
edge below the threshold, not a strategy that lost slightly. Prior
betting-simulation figures are superseded.

Full record, including the certification scope and the caution that
neither the old nor the new figures support any profitability claim:
`reports/integrity/CERTIFICATION.md` section 13, artifact
`reports/integrity/walkforward-calibration-selection.json`
(sha256 `85876ec7…`).

**The market reference could be decided by row order.** `load_market_refs`
assigned by key, so a second `CLOSING_BENCHMARK` row for the same
(game, market) silently overwrote the first. The database holds exactly
two rows for every one of **6,681** (game, market) pairs — the historical
ingest is not idempotent — and every field agrees, so last-write-wins
happened to pick an identical value. Nothing checked that, and nothing
would have noticed it changing; row order out of a bare `SELECT` is not
guaranteed. Conflicts now raise `ConflictingMarketReference` naming the
game and both values, rather than being resolved by picking, averaging,
or taking the newest — two conflicting observations of one closing market
are a data question, not a modelling one.

**One probability was not normalized like the other two.** `distribution.py`
promises that moneyline, spread and total probabilities "cannot disagree
with each other" — the reason no model can publish a moneyline
contradicting its own spread. `spread_probs` and `total_probs` divided by
the retained mass of the pmf, which is truncated to ±100; `home_win_prob`
summed the same pmf and did not. Asked the same question two ways —
`home_win_prob()` against `spread_probs(0) → cover + 0.5·push` — the
answers agree to 1e-14 at the sigma the fitted models produce (12–14,
fallback 13.5) but drift **2.3 points at sigma 50 and 10.6 at sigma 80**,
both remaining entirely plausible probabilities.

Nothing shipped was wrong, because no registered model emits a sigma in
that range. The defect was that the invariant was documented with no
stated range, nothing enforced one, and the failure is silent where it
bites. `home_win_prob` now normalizes. The docstring also claimed the 80%
interval came from the discrete pmf; it does not — it is the continuous
Normal quantile, and that is now stated rather than glossed.

**The screen displayed a superseded metric.** `team-ratings-v1` carries
two evaluations per test scope — the before and after of the
deterministic-ordering correction. Both are kept, correctly: the record is
append-only and the earlier run genuinely happened. Section 7 above states
that the earlier numbers are **superseded and must not be cited**.

The Model Audit screen was citing them. `/v1/models/comparison` ordered by
`(scope, model_version_id)` with no tiebreak, so the pair came back in
whatever order the query plan produced, and the screen kept the first row
it saw. It showed `brier = 0.21589` — the superseded value — and would
have shown either one depending on the database's mood. Found by pulling
on the screen's own "runs disagree" badge, which flagged the condition
without anyone having asked why.

The endpoint now orders by `created_at` as well, and the screen keeps the
latest rather than the first. That does not make either component the
authority on which run supersedes which — this document is — it makes the
answer stable and lets the screen say which run it is showing
("2 runs · latest shown", with both values on hover).

### Recommended next, not built here

**The edge threshold is selected on a statistic too noisy to support it.**
Exposed by the regeneration rather than by reading the code. The candidate
threshold is chosen by maximising simulated ROI over a grid on the
validation season, guarded only by `n_bets >= 20`. Validation ROI over a
few dozen bets is extremely noisy, and it does not carry:

| fold | chosen | validation ROI | test ROI |
| --- | --- | --- | --- |
| 2024 | 0.03 | +11.03% | +1.04% |
| 2025 | 0.08 | +1.31% | −17.63% |

This is not leakage: the threshold is chosen on validation and measured on
test, which is the right structure. It is that the criterion carries
almost no information, so the chosen threshold is close to arbitrary
within its grid — and the threshold decides how many candidates the system
emits, which is the single biggest lever on the simulated result.

It is the same *class* of error as the calibration defect corrected above
— trusting a number computed on a sample too small to bear the weight put
on it — but the fix is not the same, and it is a modelling decision rather
than a defect repair. Candidates: shrink toward the largest threshold
whose ROI is not distinguishable from the best; require the validation
interval to exclude zero before selecting on it at all; or fix the
threshold a priori and report ROI at it rather than selecting on ROI.
Whichever is chosen changes what the system recommends, so it should be
chosen deliberately.

**The Model Audit noise band is a rule of thumb presented as a
threshold.** The screen decides "indistinguishable from the market" by
comparing a Brier difference against `1.96 × 0.25 / √n` — a standing
assumption about per-game Brier spread, computed from nothing in the
data. It is wide in the wrong-but-safe direction: 0.25 is the spread of
Brier *levels*, whereas the band judges a *difference* between two models
scored on the same games, which is far less variable because the two are
highly correlated. So real differences get called noise; at n=285 the
band is 0.029, and `glm-ridge-v1` at +0.01182 is reported as
indistinguishable on that basis.

The engine already has the right tool and already uses it for log loss:
`week_block_bootstrap` resamples whole weeks, which is what the
within-week correlation requires. It is not applied to Brier, and no
paired model-versus-benchmark CI is stored — `ModelEvaluation.metrics`
carries `log_loss_ci90` but no `brier_ci90`.

The correct version bootstraps the *per-game paired difference* between
each model and the benchmark over week blocks, stores it alongside the
other metrics, and lets the screen use a measured interval instead of a
constant. That is additive rather than destructive, but it changes
displayed verdicts and would need the evaluation artifacts regenerated to
take effect, so it belongs with the walk-forward regeneration decision
above rather than ahead of it.

The screen now states plainly that the threshold is a heuristic and which
way it errs, so nothing currently overstates. That is a correction of the
claim, not of the statistic.

### Open decisions from the scheduler/chain/recovery read (2026-08-10)

Two findings from the line-by-line pass are DECISIONS rather than
defects, and are recorded rather than taken.

**Two TERMINAL jobs are not effect-inspected.** `closing_capture` and
`result_ingestion` are categorised TERMINAL in `JOB_CATEGORIES` but have
no entry in `_EFFECT_SOURCES`, so a recovery of either returns
`NO_PRIOR_EFFECTS_REPLAY` without inspecting anything — never reaching the
`SCOPE_REQUIRED` branch that exists precisely so a terminal job refuses to
guess. Replay is safe for both today because both handlers are idempotent
by construction (an immutable `ClosingCapture` with a uniqueness
constraint; `ingest_result` refusing a correction to an existing final),
so the decision is right and only the stated reason was false — it has
been corrected.

Registering them would make a scheduled, paramless recovery of either
demand a `canonical_game_id` and otherwise block on
`MANUAL_REVIEW_REQUIRED`. That follows the module's own stated principle,
but it would stop recoveries that currently complete safely, so it trades
correctness-of-principle against availability. `feature_snapshot`
(SNAPSHOT) is unregistered on the same footing but carries less risk.

**`missed_runs` reports slots with no run row at all**, not slots with no
*terminal* run, which is what its docstring claimed. A slot whose only
attempt failed or dead-lettered reads as not missed. Nothing in the
running system calls it — it is diagnostic only — so the docstring was
corrected to match rather than the behaviour changed. Which definition is
wanted is for whoever first needs the function.

### Open decisions from the MVP build (2026-08-11)

**`ftp-2026-v1` pins no calibration artifact.** The frozen policy carries
`calibration_version: null`, while the model registry records
`cal_none_val2024` after the out-of-fold correction. Functionally these
agree - the corrected selection IS the identity, so "no calibration" is
what the policy should apply. What is loose is traceability: a reader
cannot tell from the policy whether `null` means "identity was selected"
or "nobody decided".

Correcting it is not an edit. `freeze_policy` refuses to alter a frozen
record, by design, so pinning the artifact requires a NEW policy version,
which starts a separate evaluation cohort. The current cohort holds four
rows and nothing settled, so the cost of doing so is zero today and will
not stay that way once capture starts. Worth deciding before 2026-09-01.

**DECIDED 2026-08-13: `ftp-2026-v2` is frozen with
`calibration_version: cal_none_val2024`.** Hash `5f988ec15da9`, same window
(2026-09-01 to 2027-02-28), same model `market-residual-v1`, same a priori
edge threshold of 0.05. Nothing else changes: the identity calibration was
already what `null` meant, so this fixes traceability and not behaviour.

Taken now because the cost was zero and would not have stayed so. The
`ftp-2026-v1` cohort held four rows — three DATA_INCOMPLETE and one PASS,
nothing settled, no research candidate — so no evidence was abandoned by
starting a second cohort. After 2026-09-01 the window is open and every
row written into it would have had to be reasoned about.

`ftp-2026-v1` is not deleted and cannot be: it is immutable and its
four rows stay readable in their own cohort.

**Both policies now claim 2026-09-01 to 2027-02-28**, which is inherent to
superseding a frozen record rather than a mistake. `active_policy`
resolves it by `created_at DESC` — the most recently frozen policy
covering the date wins, which is right because a later policy is the later
decision. It resolved it SILENTLY, though: Data Health reported
"ftp-2026-v2 in force" and gave the reader no way to know a second frozen
policy claimed the same games or that recency was what settled it. The
check now names the superseded version and the rule, and stays OK, because
superseding is the documented way to change a rule and reporting it is not
alarming. Pinned by `tests/test_overlapping_policy_windows.py`.

**The forward test uses an a priori threshold, and should keep doing so.**
`build_policy_draft` fixes the research-candidate edge threshold at 0.05,
and its docstring says why: the 2024/2025 seasons are burned, so tuning
against them would contaminate the forward test before it began. That is
right, and it means the edge-threshold finding recorded above - selection
on a validation ROI that does not carry - applies to the BACKTEST only.
The forward cohort is not exposed to it.

**Docs written from Python must pass `encoding="utf-8"`.** Several
`write_text` calls in this session omitted it, so on Windows they wrote
cp1252 and put a raw `0x97` byte where an em dash belonged - invalid
UTF-8, in `model-governance.md`, `deployment.md` and `CERTIFICATION.md`.
Repaired, and noted because the failure is silent: the files render
normally in most editors and only break when something reads them
strictly.

### Noted, not changed

`MarketResidual.fit` estimates `sigma_margin` from residuals of the rows
it just fitted, so the stated uncertainty is optimistic. At ~1,650 fit
rows against 12 features the understatement is under 0.5%, which does not
justify changing a governed model; it is recorded because the direction
of the error is toward overconfidence.
