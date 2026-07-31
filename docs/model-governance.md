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
