# Release 1B.3 — predictive research and market access

Baseline: `90d18afb7e30ae8cf06e33db37b413825ed55a21`. Evaluated implementation: `4ab60d0dafa973edf57c76d9e3f294118ac440c7`. Source integration and deployment require separate review.

## Implemented research workflow

`compare_research_models.py` initializes only a **new** isolated attested SQLite workspace, captures genuine public nflverse schedule/result/closing-reference bytes through the existing ingestion/raw-artifact machinery, and loads canonical historical games with the existing loader. Optional current capture runs the existing ESPN/nflverse/NWS worker before model study records are added. No original database is adopted or migrated. Every invocation has a distinct database and immutable source artifact lineage.

```powershell
cd C:\Users\mjcun\fde-handoff-release0\apps\api
uv run python scripts/compare_research_models.py --database-file data/my_new_study.db --capture-current
```

The command writes JSON and Markdown comparison reports, existing model-registry artifacts, existing Prediction rows and ModelEvaluation rows. It reuses existing home-field/rolling/rating predictors, GameDistribution, sequential replay, calibration diagnostics and week-block bootstrap. It introduces no separate prediction registry or feature framework. Existing six-model studies and frozen evaluations are retained.

The capture worker deliberately refuses a workspace once historical evaluations exist. This protection remains unchanged. A study workspace is a captured snapshot; use a new workspace for the next batch. The API reads existing reports and never fits models, calls providers or alters evidence on GET. Source coverage/freshness still has its existing gates. Study records are not imported into the user's original profile or installed app.

## Executed comparison and its limits

Four chronological expanding-window folds test 2022, 2023, 2024 and 2025. For test season N, rating hyperparameters are compared only on N−1 after fitting seasons through N−2, then fitted on prior seasons and replayed on N. Every k trial is retained. Models use score-only histories; no retrospective closing price enters a candidate prediction. Rolling features use prior completed games; rating replay predicts before observing outcomes with deterministic simultaneous-game ordering. Every fitted model/fold and current research snapshot has a distinct registry ID, so later runs do not overwrite earlier predictions/artifacts.

1,087 regular-season games were scored; the canceled 2022 BUF/CIN game has no result and is excluded. Three ties are excluded from binary moneyline metrics, yielding 1,084 scored outcomes with two-sided closing moneylines. Pushes are excluded and counted separately for spread/total probability scores. Spread and total losses are conditional on no push. Their simple 0.5 comparator is not represented as a captured bookmaker quote. Closing handicaps and totals supply same-game point-error benchmarks; closing moneylines supply a two-sided no-vig probability benchmark.

| Approach | Moneyline log loss | Brier | Margin MAE | Total MAE |
|---|---:|---:|---:|---:|
| Home-field baseline | 0.6889 | 0.2479 | 10.694 | 10.811 |
| Rolling baseline | 0.6480 | 0.2283 | 10.118 | 10.698 |
| Team ratings | 0.6427 | 0.2259 | 10.007 | 10.514 |
| Closing reference | 0.6082 | 0.2105 | 9.494 | 10.189 |

Ratings are best among tested score-only approaches, but worse than the closing reference on all three market categories. The paired week-block 90% log-loss difference versus closing is approximately +0.0247 to +0.0451, favoring closing. Both rolling and ratings therefore **fail the market-comparison screen**. This says nothing about executable Tennessee bet365 value or profitability. The closing reference is not a competing available pregame input.

Reports retain season results, drift, Brier/log loss, reliability bins, calibration intercept/slope, spread/total conditional scores and push counts, margin/total MAE and CRPS, 80% distribution coverage, log-loss uncertainty and paired comparison intervals. The constant home-field model's slope diagnostic is weakly identified because its probability varies only between seasons; reliability/sample sizes matter more than interpreting that large coefficient. No calibrator is selected on test data; identity calibration is explicitly used. A second replay with all results withheld for 24 hours challenges the source timing approximation without retuning k.

The 500-game/common-market minimum and 72–88% tolerance for nominal 80% intervals are declared diagnostic screens, not evidence of promotion. Models must beat simple baselines and closing-reference screens before further review. Even passing these retrospective screens remains INSUFFICIENT EVIDENCE without independent prospective confirmation. No eligible or approved model is asserted here.

Historical nflverse exports are mutable; the legacy loader's kickoff+4h30m result time is an approximation, not a verified historical publication time. 2024/2025 were previously examined and cannot be called untouched. RETROSPECTIVE_KICKOFF records disclose this scope and actual capture time. The result-delay sensitivity is not a substitute for archived vintages. This release does not certify strictly point-in-time historical availability of mutable metadata, injuries or source corrections.

## App integration

War Room and Game Lab read `/v1/research/model-comparison`, display model performance, failures, market comparisons and uncertainty, and keep NO BET. Details expand within the existing layout. Current future-game research estimates have a research-only registered artifact and stored Prediction provenance; they omit unverified personnel/availability effects. They are not qualified betting probabilities. Unknown, completed, elapsed or stale game snapshots are withheld. Reports remain historical after snapshot expiry; failed refresh hides cached numerical findings. Current estimates expire after 24 hours and are never refreshed by changing their stored capture time.

Player research now shows descriptive shares of team pass attempts, carries and targets for one matched completed game. Exact canonical game/team/week and valid team denominators are required. Missing/ambiguous denominators remain unknown, and snaps/targets do not become route participation or projected opportunity. Source and row IDs remain in evidence. Normalization v8 invalidates older prepared manifests without changing them.

## Sources and next external gates

See [official availability/usage findings](release1b3-availability-usage.md), [model infrastructure audit and player-prop/SGP plan](release1b3-model-audit.md), and [current provider feasibility matrix](release1b3-market-access.md).

Official public weekly report pages can be inspected, but no accepted complete automated source with per-report issuance, canonical identity and official inactive-list completeness was established. The existing verified structured intake remains available. Secondary injury exports do not establish official completeness. Current-season routes remain missing; snap/attempt/carry/target evidence is descriptive only.

No Tennessee bet365 market or exact combined SGP quote was sample-verified. A public bookmaker catalog was fetched, not a market feed. Required external decision: obtain written Tennessee-specific coverage and retention/modeling rights, then provide an authorized sample credential with no billing activation. Confirm exact SGP offer/combinability separately; neither generic bet365 coverage nor a fair-price engine is sufficient. No provider/account/subscription was activated.

Qualified singles require accepted point-in-time personnel/source coverage, an improved market-specific model, unseen/prospective validation and actual authorized offered prices/settlement semantics. Qualified SGPs additionally need a validated joint outcome model and exact sportsbook leg eligibility/combined quote. Multiplying marginal probabilities is explicitly insufficient. No production model promotion, wagering, recommendations, master change, deployment or installed update is included.

## Evidence and preservation

[Executed comparison and screenshots](../qa/release1b3/README.md) include the full final report, both exploratory outputs, source hash, database readback and actual desktop/mobile views. The original database SHA-256 remains `133ff5b1a59229280ec19e84c59c314f200b377623b3bfa26de0fa583e2e5d0a`; all three unresolved historical evaluations and frozen studies remain unchanged. No schema migration was added. Final CI links and skips are recorded in the PR acceptance report.
