# Postgame evaluation

PASS: three persisted evaluations of the actual retained pregame predictions. Independent arithmetic in execute.py verifies stored metrics to absolute tolerance 1e-12. Inputs and independent outputs: execution.json; persisted API readback: evaluated-api.json. No forecast was regenerated with final scores.

Convention: home = Denver; all margins = Denver minus Seattle. Actual margin +3; actual total 31. Signed error = prediction minus outcome. Brier = (P(home win) - observed home-win indicator)^2. For a tie the existing evaluator excludes winner/Brier/log-loss scoring and still evaluates margin/total; fixture tests verify that policy.

| Model | Favored / probability | Winner correct | Brier | Predicted margin | Signed margin error | Absolute margin error | Predicted total | Signed total error | Absolute total error |
|---|---|---|---|---|---|---|---|---|---|
| naive-homefield-v1 | DEN 55.45% | True | 0.198505 | 1.983 | -1.017 | 1.017 | 45.486 | 14.486 | 14.486 |
| naive-rolling-v1 | SEA 56.36% | False | 0.317596 | -2.317 | -5.317 | 5.317 | 44.700 | 13.700 | 13.700 |
| team-ratings-v1 | SEA 53.93% | False | 0.290817 | -1.321 | -4.321 | 4.321 | 43.755 | 12.755 | 12.755 |

Home-field baseline was best for winner prediction, Brier and margin error. Team ratings was best for total error. All three 80% model intervals covered the observed margin and total. These are three model assessments of ONE game, not three independent outcomes, evidence of statistical significance, or established model superiority. No profitability is calculated without verified market prices and settlement rules. NO BET.
