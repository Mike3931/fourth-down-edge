# SUPERSEDED — DO NOT CITE

## `team-ratings-v1` historical evaluation, test seasons 2024 and 2025

> The original team-ratings-v1 historical evaluation is superseded because
> simultaneous game results were previously applied in input-dependent
> order. The corrected deterministic evaluation replaces those metrics. The
> change did not affect other model tiers, recommendation statuses,
> simulated wagers, or reported ROI.

The prior artifacts are **retained, not deleted**. They remain in
`model_evaluations` (the table is append-only; `ModelEvaluation.id` embeds
the run id) and in `walkforward-prefix-vs-postfix.json`. They must not be
cited.

## Record

| field | value |
| --- | --- |
| Reason | simultaneous-result absorption order depended on input list order |
| Superseding commit | `fc68cca` (Phase 2 patch branch), `e80d504` (Phase 3 branch) |
| Superseding tag | `phase-2-research-engine-v1.0.1` |
| Original tag (unchanged) | `phase-2-research-engine-v1` → `5a9b40ae` |
| Original snapshot hash | `d2db249026305a5bd117d31eb486e68af36fd97d9ac60edbd22382588cc4c38e` |
| Replacement snapshot hash | `f02ebd4f1c4236af59379a67d83a1d163bc1086a9cf18c51a72cd5dd6452c8fe` |
| Historical scan hash | `b12f4aab2239efb40a55fd67bf6eb1888e43b1352fdabf0e4beed266fa47600f` |
| Run-scoped comparison | `reports/integrity/run-scoped-comparison.json` |
| Data manifest | nflverse, seasons 2018–2025, 2227 games |
| Model version | `team-ratings-v1` |
| Feature version | `nfl-core-v1` |
| Folds | train ≤ N−2, validate N−1, test N |
| Test seasons | 2024, 2025 |
| Horizon | PREGAME |
| Generated | 2026-08-03 |

## Superseded values → corrected values

### Test season 2024

| metric | previous | corrected | absolute | relative |
| --- | --- | --- | --- | --- |
| log loss | 0.621482778369 | 0.621501795801 | +1.902e-05 | +0.00306% |
| Brier | 0.215890590267 | 0.215900239531 | +9.649e-06 | +0.00447% |
| margin CRPS | 7.39199904989 | 7.39215085008 | +1.518e-04 | +0.00205% |
| margin MAE | 10.1593060839 | 10.1596346151 | +3.285e-04 | +0.00323% |
| calibration slope | 1.35615512994 | 1.35613212831 | −2.300e-05 | −0.00170% |
| calibration intercept | −0.120180026015 | −0.120160216413 | +1.981e-05 | −0.01648% |
| log-loss CI90 low | 0.591398097890 | 0.591441259685 | +4.316e-05 | — |
| log-loss CI90 high | 0.654539573364 | 0.654551086883 | +1.151e-05 | — |

### Test season 2025

| metric | previous | corrected | absolute | relative |
| --- | --- | --- | --- | --- |
| log loss | 0.633493249615 | 0.633507364011 | +1.411e-05 | +0.00223% |
| Brier | 0.221923568383 | 0.221926447531 | +2.879e-06 | +0.00130% |
| margin CRPS | 7.24884519863 | 7.24898788926 | +1.427e-04 | +0.00197% |
| margin MAE | 10.1623205683 | 10.1628452867 | +5.247e-04 | +0.00516% |
| calibration slope | 0.96086508827 | 0.960734406007 | −1.307e-04 | −0.01360% |
| calibration intercept | −0.112844304599 | −0.112815895801 | +2.841e-05 | −0.02518% |
| log-loss CI90 low | 0.599567837182 | 0.599579099881 | +1.126e-05 | — |
| log-loss CI90 high | 0.670028615315 | 0.670020640752 | −7.975e-06 | — |

Every error metric moved **worse**. The corrected evaluation is the less
flattering one.

## Candidate counts, simulated wagers, ROI, drawdown

**Unchanged.** Verified per-run, not by collapsed key: 1140 recommendation
rows per season before and after, **0 differences** across status, line,
price, reasons, execution, and settlement — which carries the fill, ROI,
and drawdown fields. See `run-scoped-comparison.json`.

Because nothing in the betting simulation changed, no candidate count or
ROI figure is restated here. **Unchanged ROI is not evidence of
profitability** and must not be cited as such.

## Explicitly NOT superseded

| artifact | status |
| --- | --- |
| `naive-homefield-v1` | unchanged, citable |
| `naive-rolling-v1` | unchanged, citable |
| `market-benchmark-v1` | unchanged, citable |
| `glm-ridge-v1` | unchanged, citable |
| `market-residual-v1` | unchanged, citable |
| All recommendation statuses | unchanged, citable |
| All simulated wagers / ROI / drawdown | unchanged, citable |
| Calibration artifacts | unchanged |
| Model registrations | unchanged |
| Fold definitions, grids, seeds | unchanged — no tuning performed |

## Scope of the correction

Two distinct corrections were made. Only the second superseded anything.

1. **Lookahead guard** — certified as changing nothing. Snapshot hashes
   byte-identical.
2. **Deterministic ordering** — superseded `team-ratings-v1` only.

The superseded numbers were not produced using future information. They
were produced under an absorption order that depended on how rows arrived.
