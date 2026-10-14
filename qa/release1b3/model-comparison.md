# NFL singles research comparison

Run: `cmp_daa05467efa8`
Recorded: 2026-10-14T22:45:20.583576+00:00

RESEARCH ONLY — NO BET

| Approach | Games | Log loss | Brier | Margin MAE | Total MAE | Status |
|---|---:|---:|---:|---:|---:|---|
| naive-homefield-v1 | 1087 | 0.6889 | 0.2479 | 10.694 | 10.811 | RESEARCH ONLY |
| naive-rolling-v1 | 1087 | 0.6480 | 0.2283 | 10.118 | 10.698 | VALIDATION FAILED |
| team-ratings-v1 | 1087 | 0.6427 | 0.2259 | 10.007 | 10.514 | VALIDATION FAILED |

Closing moneyline benchmark: n=1084, log loss=0.6082, Brier=0.2105. Closing margin MAE=9.494; total MAE=10.189.

Closing quotes are retrospective yardsticks. No candidate receives them.

## naive-homefield-v1

- Transparent reference baseline, not a recommendation model

Log loss 90% week-block interval: [0.6832661942050758, 0.6942156974089981].
80% distribution interval coverage: {'margin': 0.8022079116835327, 'total': 0.8169273229070837}.
Calibration intercept/slope: 3.0235694005699845 / -12.987970464529713.
Paired log-loss difference vs baseline, 90% interval: [0.0, 0.0].

## naive-rolling-v1

- No paired evidence of lower moneyline log loss than closing-market benchmark
- Margin MAE does not beat closing-line benchmark on the same games
- Total MAE does not beat closing-line benchmark on the same games

Log loss 90% week-block interval: [0.6334623564951762, 0.66315737281965].
80% distribution interval coverage: {'margin': 0.8491260349586016, 'total': 0.8215271389144434}.
Calibration intercept/slope: -0.011025842432944416 / 1.0383043816388506.
Paired log-loss difference vs baseline, 90% interval: [-0.055074749094889054, -0.026450531148405638].

## team-ratings-v1

- No paired evidence of lower moneyline log loss than closing-market benchmark
- Margin MAE does not beat closing-line benchmark on the same games
- Total MAE does not beat closing-line benchmark on the same games

Log loss 90% week-block interval: [0.627835553014826, 0.6574649532584824].
80% distribution interval coverage: {'margin': 0.827046918123275, 'total': 0.828886844526219}.
Calibration intercept/slope: -0.014543562643032798 / 1.0152275290829083.
Paired log-loss difference vs baseline, 90% interval: [-0.060589229727236495, -0.03261480566745516].

## Boundaries

- RESEARCH ONLY: no model is approved for recommendations or wagering.
- Retrospective mutable public data; result receipt times are approximated, not archived historical vintages.
- 2024 and 2025 were previously studied. These are out-of-season folds, not untouched confirmation data.
- Closing prices are evaluation-only benchmarks, never candidate inputs or verified bet365 offers.
- Score-only models omit official availability, quarterback changes, routes and matchup personnel.
- Normal margin/total distributions approximate key-number tails; no SGP joint-probability claim.

Full JSON retains all season folds, reliability bins, spread/total metrics, selected hyperparameters, prior-validation sensitivity, 24-hour result-delay sensitivity, and registered model identities.
