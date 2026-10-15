# Release 1B.4 — prospective game-day research

Baseline: `69a2a47b5a114456343d984e5a65b3a680a9494f`. Research only; NO BET. Source integration, deployment and installed updates are outside this release.

## Run in a new authorized workspace

From `apps/api`:

```powershell
uv run python scripts/init_research_workspace.py --database-file data/my_new_prospective.db --prospective
uv run python scripts/run_research.py --database-file data/my_new_prospective.db --prospective --cycles 8 --interval 900
```

No services are installed. The existing foreground worker lock, 96-cycle maximum, polling/backoff and minimum 300-second interval apply. Only explicitly initialized PROSPECTIVE workspaces permit capture alongside prospective evaluations. Existing attested capture workspaces cannot be upgraded by editing a marker: purpose is recorded immutably in the database and matched to file identity/token/marker. Original profiles, copied/renamed profiles, frozen backtest studies, legacy evaluations and ledger-bearing databases remain refused. No populated original database is adopted or migrated.

Default checkpoint windows are early-week (168h), 72h, 24h and final (90m). `--checkpoint-hours EARLY H72 H24 FINAL` configures their hours. Only the most recent due window is attempted; missed horizons are not backfilled. Final capture requires complete official availability and fresh schedule evidence, so it remains withheld under the currently incomplete feed. The actual execution timestamp is stored, never the intended checkpoint time. `--skip-official-capture` remains a compatibility flag; automatic NFL.com retrieval is disabled regardless of this flag. No runtime setting enables it.

## Existing analytical infrastructure reused

This extends the generic ModelVersion, FeatureSnapshot, Prediction and ModelEvaluation workflow, rather than adding a parallel prediction registry. Existing home-field, rolling and dynamic-rating approaches supply score-only research baselines. Ratings k=.12 is fixed in source, not retuned against future results. No price enters any forecast. Historical failed-family evidence remains visible, new model versions remain research_only, and nothing promotes a model.

Training uses only immutable schedule/final observations actually received before execution. Only uncontested ESPN/nflverse final observations enter; future receipt/publication times and identity/result reviews are excluded. At least 32 final games are required. In prospective mode the existing public nflverse schedule download also supplies prior regular seasons from 2010 onward. A separate immutable `schedule_history` source retains original bytes, semantic selection hash, actual receipt time, per-season normalization outcomes and result refusals. It makes no claim of complete historical coverage or archived point-in-time availability. Unchanged historical selections are not normalized again when current games or odds change. Historical score disagreements remain reviews, never automatic corrections. These past results support forecasts created now; they do not create past predictions or prospective performance. Missing personnel, QB, route, efficiency and weather effects remain explicit. Outcome uncertainty uses existing marginal distributions/80% ranges, not a claim of calibrated real-money confidence or joint SGP probability.

Frozen feature records contain training result IDs and values, exact schedule revision, training fingerprint/cutoff, source versions, captured research, missing inputs and checkpoints. Registry artifacts retain parameters, code/dependency hashes and limitations. Unchanged evidence retries reuse the completed feature record; changed evidence/checkpoint/training/config/code creates a new version. Scoped database triggers reject UPDATE and DELETE of prospective predictions, features, model versions and evaluations in SQLite and PostgreSQL. Other historical rows are not rewritten or adjudicated.

Each capture phase retains its existing Job audit. The subsequent prospective phase has a separate bounded Job and transaction: failed fitting/publication rolls back all forecast rows; source capture failure prevents new forecasts but permits already-known uncontested finals to be evaluated. A game crossing kickoff during actual forecast execution causes rollback. No CLI historical-time override exists. Controlled injected clocks are test-only.

## Results and performance

After an uncontested public-provider final arrives, per-prediction ModelEvaluation records append. A known conflict withholds both settlement and previously scored results from current aggregates without deleting them. A reasoned same-provider correction creates a new final observation and a new evaluation. Independent conflicting providers never resolve by newest-row order. This is public-result research, not licensed sportsbook settlement verification.

Ties are excluded from binary moneyline scores, counted separately, and retained for margin/total errors and interval coverage. Cancelled/postponed games stay unevaluated. Schedule revisions retain one canonical identity and frozen original kickoff; a forecast must precede both its recorded kickoff and the subsequently known played kickoff. Reports select the latest eligible evidence version for each game/approach/horizon, retaining every earlier version and correction. Horizons are repeated cohorts, never summed as independent games. Brier/log loss, reliability, margin/total MAE, interval coverage and week/season/horizon groups are read from frozen predictions and current uncontested evaluations. Calibration slope/intercept require at least 100 binary outcomes and two classes; even larger samples remain INSUFFICIENT EVIDENCE, never automatic promotion.

`GET /v1/research/prospective` and its `canonical_game_id` filter are authenticated read-only views. Both existing pages show a compact forecast/performance panel with expandable missing information, all versions, changes, results and week/season details. Failed refresh/schema validation hides cached estimates. Completed, revised, superseded, disputed and expired estimates remain historical records, not current pregame claims. NO BET remains visible.

## Official availability

Automatic NFL.com injury-page collection is disabled pending documented prior written authorization. See [the enforced access boundary, exact authorization requirement and alternative-source investigation](release1b4-official-access.md). The retained offline parser handles original bytes/hash, actual receipt time, practice/game designations by team/week and NFL player slug. Explicit existing NFL-slug-to-GSIS bindings may link players; names never create bindings. Unmapped evidence remains visible and does not partially publish a player identity. Ambiguous team/week game linkage is withheld. Raw content and identity/schedule context determine new immutable evidence versions.

The page does not establish per-report issue times or a complete official inactive feed. HTTP artifact timing is not substituted for issue time. OUT does not become INACTIVE; an absent row does not become healthy/active. `official_reports` remain OFFICIAL_PAGE_UNTIMED, separate from verified structured reports, secondary nflverse injury evidence and model assumptions. Complete availability remains false and final-checkpoint publication stays blocked. Source outages retain earlier observations with refresh failure labels.

## Model experiments and external gates

`scripts/ablate_research_features.py` creates only a NEW study from preserved real nflverse games CSV. It reuses CoreV1Builder, RidgeBaseline, prior-season alpha comparison, prior-only Platt calibration, registry and evaluation metrics. Rest-only versus intercept-only ablations retain all trials, folds, calibrated/uncalibrated losses and uncertainty. Already inspected 2022–25 seasons are explicitly exploratory reused diagnostics. Mutable historical metadata is not proof of archived PIT availability. No candidate is promoted or automatically added to prospective forecasting based on these results. EPA/pace/personnel/weather additions require accepted multi-season point-in-time inputs before numerical effects are justified.

See [Tennessee pricing decision](release1b4-market-access.md) and [SGP prerequisites](release1b4-sgp-prerequisites.md). No TN market or exact combined SGP quote is accepted. Authorized rights and representative samples are required before an adapter; no subscriptions, outreach, credentials or restricted access were activated.

Execution evidence, final test counts, actual captured pregame records, restart/source-addition preservation checks and remaining limitations will accompany the PR acceptance report. Actual future outcomes cannot be manufactured to complete validation; prospective performance remains empty until real uncontested finals arrive.

