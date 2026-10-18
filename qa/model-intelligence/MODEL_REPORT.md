# Fourth Down Edge â€” model development and validation

Recorded 2026-10-18T12:16:56.095135+00:00. Base d3fed08513625aa5c9853480a1ec0496f88d4cdc. RESEARCH ONLY â€” NO BET.

## A. What was wrong with the original models?

The supplied retrospective results reproduce exactly on 1,087 regular-season games (2022â€“2025), with 1,084 non-tie moneyline outcomes and 72 season-week blocks. Ratings logloss0.642745 vsclosing0.608190 is a0.034555 gap; Brier0.225865 vs0.210455 is a0.015409 gap. MarginMAE10.007489 vs9.494480 differs0.513009 points; totalMAE10.513761 vs10.189052 differs0.324708 points. Closing prices are retrospective yardsticks, not available pregame features or executable bet365 offers.

We can quantify this gap and reject specific proposed explanations; this observational dataset cannot identify its complete causal source. Missing personnel information is a plausible deficiency, not a measured causal attribution.

| Priority / classification | Evidence | Consequence and action |
|---|---|---|
| P1: proven data/validation limitation | Public historical files captured in2026; result availability approximated as kickoff+4h30m | Historical chronological replay is exploratory, not archived point-in-time or prospective proof. Preserve actual captured future vintages. |
| P1: plausible information deficiency | Active models are score-only; historical team efficiency data never enter the three prospective models | Test incremental EPA information rather than presume improvement. This experiment did NOT support adding this particular correction. |
| P1: validation limitation |2024/2025 and now all study seasons inspected; multiple research attempts | Retain all attempts; no untouched-validation or significance/promotion claim. Fixed new cohort declared before future outcomes. |
| P2: plausible architecture deficiency | Rating totals update both scoring states from total-score error, without offense/defense decomposition | First candidate tested this; it worsened every primary error, rejecting this particular replacement. |
| P2: proven legacy hazard, not demonstrated live failure | DynamicRatings._reset leaves learned HFA; reused instances can carry state across fits | All study fits instantiate fresh models. Shared active model source intentionally unchanged. |
| P2: proven method mismatch | Legacy ratings replay advances at kickoff; rolling uses90min pregame | New comparisons align ratings and candidate at90min; original kickoff study reproduced separately. Ratings metrics happen to match; rolling differs slightly because new score-only history excludes postseason rows. |
| P2: calibration selection limitation | Legacy calibration uses seeded nonchronological out-of-fold selection | No calibrator is selected in these experiments; scales use earlier-season prequential residuals. Existing ratings calibration slope 1.015 is already close to1, so generic recalibration is not demonstrated as the missing advantage. |
| P2: distribution limitation | Independent normal marginal distributions, fitted scales, no joint score/SGP model | Report CRPS, pushes, interval coverage, calibration and key-number diagnostics; no SGP/prop probability or profitability claim. |
| P3: unsupported feature proposals | No archived pregame QB starter, complete official injury, personnel, routes, weather or travel-vintage evidence established | Excluded; no numerical adjustments or healthy/active assumptions. |

## B. What was implemented?

1. OpponentScores: decayed ridge offense/opponent-defense effects, HFA and league scoring. Primaryalpha25/365dayhalf-life;180/730days andalpha10/100 retained sensitivities. Explicit cutoff, self-exclusion, finite-data guards, source/order invariance, research registry, frozen inputs/artifacts. Uncertainty uses earlier-season prequential residuals. All five configurations failed to outperform ratings.

2. EfficiencyResidual: existing dynamic ratings plus a regularized correction from24 lagged features: offensive/defensive sums and differences in EPA/play, EPA/dropback, rushingEPA, success rate, interception rate and plays. Primaryalpha100/365days;alpha25/400 and180/730days retained. Fit standardization/imputation/correction on earlier seasons only. The predictor refuses reviewed/disputed inputs and future training cutoffs, ignores closing prices, and falls back exactly to unmodified ratings when all stat inputs are missing.

Data: existing licensed nflverse schedule release, sha256690d9e1530c8e29477ebda065979e8c0f43ef6b3c632cf4eafca654c20680a76, with retained capture receipt/CC-BY-4.0 attribution. Team aggregates were exported READ-ONLY from protected originalfde.db:4454 rows, 2018â€“2025, zero game-join orphans. All17 measured keys are non-null in 4,454 rows except neutral EPA 4,453/4,454; neutralEPA is not a candidate regressor. Original observed timestamps and result visibility both gate lagging. All 26,088 scored candidate stat cells are present;0/26088 missing. Current historical revisions cannot be relabeled original publication vintages.

No new dependency, endpoint, worker integration, frontend redesign or deployment. Both failed candidates and 497 registry artifacts remain in new attested study workspaces, with 2,174 timestamped retrospective candidate prediction/feature snapshots. The score-only study also retains its original baseline models/predictions. No model was promoted.

## C. Did it work?

**No predictive improvement was established. Keep the current ratings model.** The EPA candidate is shown below because it is the second justified experiment, not because it won a selection contest.

| Metric | Existing best: ratings | EPA candidate | Candidate minus ratings |
|---|---:|---:|---:|
| Moneyline logloss | 0.642745 | 0.645469 | +0.002724 |
| Brier | 0.225865 | 0.226723 | +0.000858 |
| MarginMAE | 10.007489 | 10.044761 | +0.037272 |
| TotalMAE | 10.513761 | 10.588361 | +0.074600 |
| Calibration intercept | -0.014465 | -0.036028 | -0.021563 |
| Calibration slope | 1.015218 | 0.901004 | -0.114213 |
| MarginCRPS | 7.248048 | 7.262502 | +0.014455 |
| TotalCRPS | 7.500623 | 7.529777 | +0.029155 |
| 80% margin coverage | 82.70% | 80.50% | -2.21 percentage points |
| 80% total coverage | 82.89% | 80.50% | -2.39 percentage points |

Paired90% week-block intervals (candidate minus ratings; negative favors candidate):

| Metric | Mean difference |90% interval |
|---|---:|---|
| log_loss | +0.002724 | [-0.002206, +0.007586] |
| brier | +0.000858 | [-0.001162, +0.002918] |
| margin_mae | +0.037272 | [-0.033545, +0.109331] |
| total_mae | +0.074600 | [+0.008704, +0.137828] |

Versus closing market, candidate moneyline loss difference90% interval is[0.02585891752656527, 0.049681709120345835]; closing beats the candidate. No market-superiority claim. Moneyline metrics exclude three ties; margin/total errors retain them. Spread/total push counts and conditional non-push logloss/Brier are in full JSON; an evaluation line is never an input.

### All attempts, not just a winner

| Experiment | Configuration | Logloss | Brier | MarginMAE | TotalMAE |
|---|---|---:|---:|---:|---:|
| score-decomposition | primary | 0.657224 | 0.232476 | 10.198037 | 10.607712 |
| score-decomposition | short-memory | 0.657831 | 0.232711 | 10.185025 | 10.559872 |
| score-decomposition | long-memory | 0.661307 | 0.234482 | 10.303863 | 10.670785 |
| score-decomposition | weak-shrinkage | 0.652215 | 0.230165 | 10.167138 | 10.586694 |
| score-decomposition | strong-shrinkage | 0.673609 | 0.240307 | 10.460626 | 10.698409 |
| EPA residual | epa-primary | 0.645469 | 0.226723 | 10.044761 | 10.588361 |
| EPA residual | epa-low-alpha | 0.646316 | 0.227104 | 10.068786 | 10.624815 |
| EPA residual | epa-high-alpha | 0.644677 | 0.226402 | 10.018333 | 10.550615 |
| EPA residual | epa-fast | 0.644126 | 0.225915 | 10.026924 | 10.554023 |
| EPA residual | epa-slow | 0.646706 | 0.227403 | 10.053915 | 10.587489 |

### Season stability

| Season | Ratings logloss | EPA logloss | Ratings totalMAE | EPA totalMAE |
|---|---:|---:|---:|---:|
| 2022 | 0.651054 | 0.649661 | 10.836232 | 10.931218 |
| 2023 | 0.655389 | 0.661924 | 10.625231 | 10.665848 |
| 2024 | 0.626051 | 0.622609 | 9.994036 | 10.105287 |
| 2025 | 0.638563 | 0.647737 | 10.600729 | 10.652351 |

### Attempts to disprove improvement

First-candidate paired intervals are positive for all four primary errors, including totalMAE[+0.008927,+0.180130]. Its calibration slope 1.620 indicates underconfident probabilities in this retrospective cohort; weaker shrinkage helped but still lost. This is evidence against this fixed specification, not proof offense/defense modeling can never work.

EPA residual totalMAE worsens in every year. Logloss improves in2022/2024 and worsens in2023/2025, exposing season instability. Stronger shrinkage moves toward the existing ratings and improves on the primary correction without beating ratings. The result does not justify tuning a larger search on these same seasons.

Half the EPA features suppressed: logloss0.644486, totalMAE10.592282 (synthetic missingness stress, not observed missing cells). All features suppressed: exact ratings fallback, logloss0.642745 and totalMAE10.513761. Suppressing features improves probability error over the full correction, reinforcing the lack of incremental evidence. It does not validate injury/QB resilience: those inputs are excluded and their impact is unknown.

First-candidate24h result-delay sensitivity yields logloss0.657302 and totalMAE10.607885; its rejection survives the changed information clock. Key-number, close-final and early-season diagnostics, reliability bins and calibration intercept/slope remain in artifact-audit/full comparison JSON. They are diagnostic slices, not separately untouched tests. No probability-calibration optimization on the test season.

Artifact hashes and feature hashes verified, and all 1,087 predictions per candidate exactly replayed from stored artifacts plus frozen inputs (493 score-candidate snapshots;4 EPA seasonal artifacts). Independent arithmetic matches stored loss/Brier/MAEs. Future/own-outcome mutation tests, late source receipts, canonical/disputed joins, missing features, cutoff chronology, pickle/order reproducibility and existing-workspace refusal are covered by22 candidate regressions. Focused existing-model/research/replay/checkpoint suite:61 passed with0 skips; candidate suite22 passed with0 skips (overlapping suites, do not sum). Mandatory PR CI provides final full-suite evidence; see PR checks rather than treating unit tests as predictive validation.

## D. Remaining limitations

- All results exploratory; no untouched prospective candidate outcomes. Historical receipt times are approximations and original publication revisions cannot be reconstructed.
- Neither candidate beats ratings or closing. Bayesian claims, useful SGP probabilities, active player props, betting value or profitability are unsupported.
- Known pregame QB/availability/personnel inputs remain unavailable; weather forecasts/travel metadata require historical-vintage validation. Closing benchmark lacks executable bookmaker guarantees.
- Only four seasons/72 week blocks;90% intervals are exploratory and not corrected for research multiplicity. Model families and configurations are correlated, not independent replications.
- A time-delay stress was completed for the score candidate. A full EPA24h-delay retraining study is not claimed; the second candidate is already rejected, and partial/all feature suppression was measured instead.
- Candidate predictions are labeled RETROSPECTIVE_PREGAME and created at actual run time; they are not backdated prospective forecasts or replacements for2026 weekend records.

## E. Next experiment and independent validation

The most useful next step is a preregistered test of canonically linked, actually captured pregame quarterback-change/usage evidence against ratings. The basis is specific: generic score decomposition and EPA corrections failed, so adding more correlated team-rate columns is not currently justified. First prove a permissible source, GSIS identity, historical participation versus actual starter distinction, coverage and timestamp validity. Do not assign a numerical QB/injury adjustment without that evidence. If that source gap remains, keep collecting the current fixed baseline prospectively instead of expanding infrastructure.

The frozen future-cohort declaration contains 179 canonical2026 weeks7â€“18 IDs,90min cutoff, fixed comparators, explicit missed/withheld/disputed accounting and no closing inputs. It is a protocol, not an activated collector or collected sample. Both candidates remain rejected; no candidate is silently selected for deployment. A qualification gate requires at least 200 independently verified common games, so this season alone cannot meet it: separately preregister a later cohort before its outcomes. Preserve the179-ID denominator and all failures. Independent review, paired week-block improvement, calibration/coverage stability and archived point-in-time vintages remain required before any promotion.

## Reproduction

Use a new nonexistent database for each run; existing databases are refused. Inputs are read-only; no provider request is made. See apps/api/scripts/study_opponent_scores.py and audit_model_study.py. The licensed receipt and source hashes are retained locally under each isolated workspace/raw. Exported historical stats SHA25658ef3b088fee1c8198618f5a3423e232e52f964961854738c8918c31af6f49ff; original protected database SHA256133ff5b1a59229280ec19e84c59c314f200b377623b3bfa26de0fa583e2e5d0a. Source computation commits:a90e221(score),a1c2af9(EPA); later changes add guard tests and clarify aggregate fold metadata, without overwriting these runs.

Full outputs: baseline-reproduction.json, score-comparison.json, efficiency-comparison.json, both per-game.csv files, both artifact-audit.json files. All unsuccessful configurations remain. New model artifacts and SQLite workspaces stay local and are not shipped. No installed app, active worker checkout, collector, checkpoint or live registry was changed. NO BET remains.

Exact offline commands from the isolated checkout (use new filenames for every rerun):

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
uv run --project apps/api python apps/api/scripts/study_opponent_scores.py --database-file apps/api/data/score_rerun.db --licensed-games-file C:/Users/mjcun/fde-handoff-release0/qa/weekend-20261018/licensed-games.csv --source-receipt C:/Users/mjcun/fde-handoff-release0/qa/weekend-20261018/licensed-schedule-capture.json
uv run --project apps/api python apps/api/scripts/study_opponent_scores.py --database-file apps/api/data/epa_rerun.db --licensed-games-file C:/Users/mjcun/fde-handoff-release0/qa/weekend-20261018/licensed-games.csv --source-receipt C:/Users/mjcun/fde-handoff-release0/qa/weekend-20261018/licensed-schedule-capture.json --efficiency-stats-file apps/api/data/historical-team-stats.json --stats-sha256 58ef3b088fee1c8198618f5a3423e232e52f964961854738c8918c31af6f49ff
uv run --project apps/api python apps/api/scripts/audit_model_study.py --database-file apps/api/data/efficiency_study_20261018.db --output apps/api/data/replay-new-receipt.json
```

The original source CSV, receipt, aggregate export and model artifacts are preserved locally; mutable future downloads with a different hash are refused. Study horizons are explicitly retrospective and created_at is actual execution time. The retained first-run fold evaluation refers to an aggregate of member snapshot models rather than a single static artifact; current code records all member-version IDs explicitly. Old evidence is preserved, not rewritten.

Raw provenance check: all eight retained 2018–2025 nflverse play-by-play artifacts match their original ingestion SHA256 values. See pbp-provenance.json. They were captured in August2026, not at each historical game cutoff.

Final guard hardening also rejects nonfinite/missing frozen baseline moments and invalid uncertainty before emitting a probability; the22 candidate tests pass, and all1087 EPA predictions still exactly reproduce after this guard. This changes no fitted coefficient or study result.

Integrity scope: the existing database UPDATE/DELETE triggers protect PROS_* predictions and prospective-scores-v1 features; they do not enforce generic DML immutability for these explicitly retrospective candidate rows. This workflow never rewrites them and refuses existing study targets. Frozen report/input/artifact hashes detect changed evidence and replay all predictions; this is not a claim of prospective attestation or universal database write protection. Any actual future candidate cohort must use the existing prospective freezing safeguards in a separately reviewed integration.
