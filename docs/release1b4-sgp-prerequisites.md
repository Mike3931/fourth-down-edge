# Release 1B.4 — SGP evidence and joint-model prerequisites

Reviewed 2026-10-14. This document scopes future research; **no SGP probability or recommendation engine is implemented or approved**. Exact authorized bet365 Tennessee combined prices remain a separate prerequisite from modeling. See [market-access findings](release1b4-market-access.md), the preserved [model audit](release1b3-model-audit.md), and [usage-source assessment](release1b3-availability-usage.md).

## Available evidence and remaining gaps

| Component | Evidence available to investigate now | Limitation before a joint forecast |
|---|---|---|
| Team scoring | Existing canonical game results; ratings, rolling and home-field score benchmarks; margin/total residual and interval diagnostics | Current discretized normal marginals do not enforce feasible joint team scores or represent touchdown/field-goal mixtures. Ratings failed the closing-market comparison; retrospective improvements do not qualify an SGP. |
| Pace and game script | [Public play-by-play](https://nflreadr.nflverse.com/reference/load_pbp.html) supports lagged plays, drives, clock, score-state, pass/rush choices and efficiency research | Public availability is not proof the required multi-season, point-in-time feature matrix is installed. Later stat corrections require captured vintages. Simulating independent team play counts ignores possession and clock constraints. |
| Passing/rushing opportunity | Captured canonical player/team statistics and observed attempts/carries/targets; `forward/usage_evidence.py` explicitly reports same-game numerator, denominator and lineage | One-game shares are descriptive, not projected usage or a validated count distribution. Snaps are not routes. Missing or ambiguous denominators stay unknown. QB changes, trades and role changes require as-of roster/availability linkage. |
| Player production | [nflverse player statistics](https://nflreadr.nflverse.com/reference/load_player_stats.html) document game/week production suitable for outcome and lagged-baseline research | Outcome completeness, participation/DNP semantics, zero versus absent records, role history and revisions need acceptance across seasons before market probabilities. A weekly yardage mean is not a predictive distribution. |
| On-field personnel/routes | Historical [participation data](https://nflreadr.nflverse.com/reference/load_participation.html) can support personnel/dependence research with attribution | From 2023 onward the FTN export arrives after postseason, not during the season. It cannot establish current-week 2026 routes. A play-level route field alone does not establish a complete per-player routes-run denominator. Current route evidence requires separately authorized coverage or explicit deferral. |
| Touchdowns | Actual scoring outcomes and scorer links can be investigated with canonical identities | Passing TD and anytime scorer contracts differ. Need team scoring opportunity, red-zone role, participation hurdle and sparse-event calibration, including defensive/special-teams scores and multiple-TD tails. No TD model validated. |
| Injuries/personnel | Existing mapped, timestamped official/secondary evidence and unknown-state handling | Complete official team/game practice, designation and inactive coverage must be demonstrated separately. OUT is not official INACTIVE; omitted rows are not healthy. An unresolved identity cannot receive a name-based inferred binding. |
| Sportsbook contract | Public bet365 documentation explains dependent combined pricing; [NJ SGP help](https://help.nj.bet365.com/s/en-us/sports/bet-builder) illustrates conditional void/repricing behavior | No accepted TN leg inventory, combinability, exact combined quote, expiry or authoritative TN settlement contract. NJ behavior must not be assumed to apply to TN. A product of odds or marginal probabilities is not an exact offer or an independently estimated joint probability. |

The [nflverse update schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html) documents postgame updates/corrections and delayed participation. Preserve actual application receipt independently of a source's publication time. Newly downloaded historical files cannot retrospectively certify what was known before an old kickoff. Historical FTN participation requires FTN Data via nflverse attribution under the documented CC BY-SA 4.0 license; access rights for any additional source require separate review.

## Proposed dependency structure to evaluate

This is a testable research design, not a selected or implemented model:

1. Model shared possessions, plays and game-state evolution; learn team offense/defense and opponent effects only from earlier available outcomes. Preserve home/away, rest and weather where those inputs pass ablations.
2. Condition pass/rush decisions on those shared states. Allocate team opportunities to eligible mapped players through uncertain role/participation distributions. Enforce that player allocations cannot exceed their team's modeled opportunities.
3. Generate conditional completions, catches, yards, turnovers and scores from the same simulated events. Enforce catches no greater than targets, coherent passing/receiving production with explicit definition differences, and score conservation including defense/special teams, conversions and overtime. Do not invent correlations to make attractive combinations.
4. Represent availability/role uncertainty explicitly. If an essential report is absent, either withhold the research estimate or show a declared scenario envelope; do not silently convert missing reports to active participation.
5. Settle each exact line/period/player contract from a common simulated game, accounting for pushes, DNP/void states, overtime, mutually exclusive legs and changed lines. Keep win/loss/push/void outcome probabilities distinct. Quote-time contract versions must determine settlement.

Inference from current evidence: opportunity-count models are the smallest credible first player experiments. Begin QB attempts, RB carries and receiver targets/receptions against rolling-role baselines; compare overdispersed count/hurdle alternatives using prior-only fitting. Yardage, TD tails and joint SGP combinations follow only after those marginals are validated. No current data catalog establishes those models' reliability.

## Required experiments and acceptance evidence

- Predeclare future cohorts, horizons, feature versions and model candidates. Existing inspected 2024/2025 seasons remain development/reproducibility evidence. Do not repeatedly select against them and relabel them unseen.
- Audit identity coverage, unavailable/zero outcomes, source freshness and missingness by game/player/season. Fit transformations, personnel assignments, calibration and hyperparameters strictly within chronological training/validation boundaries. Capture immutable pregame inputs and model artifacts.
- Establish proper marginal losses, held-out threshold calibration, interval coverage and sample counts before measuring joint events. Retain failed experiments and paired comparisons against simple baselines. Shared-game and weekly dependence require grouped uncertainty estimates rather than treating correlated legs as independent trials.
- Evaluate joint event frequency/calibration by leg count, market pair, same-player dependence, positive/negative dependence, rare TD combinations and injury/role regimes. Compare against explicitly labeled independence and conservative reference baselines; the independence product cannot become the delivered joint estimate.
- Validate distribution coherence and tail sensitivity, including impossible combinations, ties, cancellation, postponed/rescheduled games, disputed results, DNP/void legs and quote rejection. A Monte Carlo standard error measures numerical sampling precision, not model correctness or total predictive uncertainty.
- In a frozen prospective cohort, preserve the full submitted leg set, eligible/accepted subset, actual authorized TN quote and times, model version, joint probability distribution, uncertainty, eventual authoritative results and appended correction lineage. Evaluate pricing only after permitted exact quotes exist; another book or a calculated fair price cannot fill the gap.

## Readiness decision

Can proceed now: strengthen point-in-time singles, establish complete official availability coverage, audit existing opportunity outcomes, and predeclare small marginal-model experiments. No broad infrastructure replacement is required.

Needs additional access: authorized bet365 Tennessee singles/alternate/TD inventory and exact SGP quotes/rules; current routes/participation if the candidate model requires them. A route-free model is possible research, but must declare that limitation and pass validation rather than impute routes from snaps.

Needs model evidence: validated market-specific marginals, coherent shared-game dependencies, tail/settlement tests and prospective joint calibration with sufficient samples. **NO BET remains enforced until sportsbook access, identity, availability, source timing, marginal/joint validation and independent approval all pass.** Forecast production and engineering tests are not proof of predictive edge.
