# Release 1B.4 acceptance evidence

Baseline: `69a2a47b5a114456343d984e5a65b3a680a9494f`. Forecast-generation implementation: `c0e3fcc5ae539638f7f04406b20311f5590950a4`. Subsequent changes batch read-side lineage, label disputed results explicitly, use historical controlled test dates and refresh the panel automatically; they do not change the recorded model outputs.

## Actual upcoming NFL forecasts

[Forecast evidence](forecast-evidence.json) contains captured pregame records, model parameters/artifact hashes/versions, frozen selected-game training/source evidence, and integrity readback. No demonstration records entered the real-data workspace.

The fresh attested final workspace captured seven nflverse datasets, ESPN fixtures/published price observations and NWS forecasts. It trained three existing score-only approaches on 79 observed uncontested results, then recorded 42 forecasts for 14 upcoming games. A process stop/restart returned the same 42 records. Adding the actual official NFL injury-page source and refreshed NWS evidence created another 42 versions while preserving every original row. Models were reused: the final workspace contains three registered models, 28 immutable feature snapshots and 84 predictions. An identical forecast-phase retry produced **zero new forecasts** and reused all 14 completed game evidence versions.

Selected game in the JSON: `2026_06_NYJ_NE`. Source capture and forecasts were recorded on October 15 UTC / October 14 Central, before the actual upcoming kickoffs. Every stored creation/cutoff time precedes its frozen kickoff. The API retains timestamps and model versions. There are **zero actual prospective evaluations**: real games have not finished. These records demonstrate publication/preservation, not predictive success.

Official source: 284 observed rows, 25 teams represented, zero accepted NFL-slug-to-GSIS bindings, unknown per-report issue times, incomplete official inactive coverage. Rows without unique game linkage or explicit player binding remain unresolved. No OUT entry became INACTIVE, and missing entries did not become healthy/active. Final-checkpoint generation remains withheld pending adequate official evidence.

The source addition also captured genuine changed NWS evidence for two games; weather remains an unvalidated observed input and changes no numerical model effect. Scores and player/availability evidence were not invented or edited for this demonstration.

## Challenges and retained unsuccessful runs

A preliminary live acceptance run exposed a retry defect: numerically identical integer versus floating-point checkpoint configurations produced different hashes. It was rejected, retained separately and corrected with normalization plus a regression. The final workspace was created fresh after that fix; its zero-duplicate retry is the reported acceptance result. No pilot or frozen research was deleted or rewritten.

A first rest-ablation execution was interrupted because it computed irrelevant historical feature aggregates. Its separate workspace/raw artifacts remain retained. The completed run below computes only the existing builder's required schedule inputs, preserving the actual historical source hash and chronological study design.

## Model experiment

[Rest ablation](rest-ablation.json), run `abl_51de3b8c273f`, retains eight model/fold evaluations, both prior-season alpha trials per fold, prior-only Platt calibration, raw/calibrated metrics, calibration parameters, week-block intervals, reliability and interval coverage. Intercept-only and rest-only ridge use the same folds and prior data. Across 1,084 non-tied 2022–25 outcomes, weighted raw log loss is 0.688950 versus 0.688653 and Brier 0.247904 versus 0.247757. This small, uneven diagnostic difference is not a defensible prospective gain and does not establish improvement over the existing ratings model or market. No feature/model was promoted or automatically added to forecasting. These previously inspected seasons are explicitly reused exploratory periods, not unseen confirmation. Efficiency, personnel, QB, pace and weather effects lack accepted historical feature vintages here.

## Tests and limitations

The added controlled **2020** fixtures exercise observed-time cutoffs, settlement/corrections, conflicting providers, ties, cancellations/postponements, schedule revisions, immutable SQL guards, restart, missing availability, source outages, fit failures and integer/float retry equivalence. They are separate test-only databases, never actual prospective evidence. PostgreSQL has an additional raw-SQL immutability gate. Frontend contracts cover research-only estimates, uncertainty, missing inputs, NO BET, mobile overflow, malformed/promoted payload refusal and failed-refresh cache hiding.

Actual prospective live-engine acceptance: eight passed; the legacy historical-comparison opt-in is intentionally absent from this new workspace. It is verified separately against the preserved Release 1B.3 study. Ten real-artifact international-slate cases passed. Final full CI results, exact head and skips are linked in PR #6 after completion; source integration is not included in this PR.

Screenshots: [War Room desktop](war-room-desktop.png), [Game Lab desktop](game-lab-desktop.png), [Game Lab mobile](game-lab-mobile.png). The source application retains its existing layout; no installed app or user application process was changed.

## Final multi-season acceptance, October 15

[Multi-season evidence](history-forecast-evidence.json) records generation under `3469038cb464bcf31600d31811b06010fe8ecb99`. The worker reused its permitted nflverse schedule download to capture 4,175 prior REG games from 2010–25 at the actual October 15 receipt time. Together with 79 current-season finals, 4,254 observed uncontested results supported 42 new forecasts for 14 upcoming games. No historical predictions were created. Historical venue warnings remain recorded; score-only training does not use those venue attributes. Coverage is not asserted complete merely because a file downloaded.

All 168 previous forecasts, their frozen features/model versions, all existing research sources/facts, game references and schedule observations passed per-row hash preservation. The workspace now retains 210 forecasts, 70 feature snapshots and 12 registered versions (three approaches across four training/code vintages). An immediate forecast retry created zero records and reused all 14 game evidence versions. Artifact hashes matched. Integrity and foreign-key checks passed; all actual prospective evaluation counts remain zero.

For the real SEA at DEN fixture, forecasts were recorded at `2026-10-15T10:55:09.628124Z`, ahead of kickoff `2026-10-16T00:15:00Z`. The ratings estimate is DEN win 46.1%, margin −1.3 and total 43.8; its 80% ranges are margin −18.5 to +15.8 and total 26.4 to 61.1. This historically failed research family remains ineligible for recommendations. These estimates demonstrate frozen output, not validated accuracy. Current desktop/mobile screenshots show this later multi-season vintage, while the original JSON evidence remains unchanged.

Three added history regressions cover early-season training with no current-year finals, actual receipt-time visibility, no historical forecasts, repeated/irrelevant-current-market changes without duplicates, immutable score-conflict retention and worker integration using one public download. Lint and all 89 typed source modules passed locally. The updated real API/browser journey passed eight tests, with one intentional legacy-study skip. That legacy path separately passed all nine live-engine tests against the unchanged Release 1B.3 study. Final mandatory CI evidence is recorded on PR #6 for its exact reviewed head.

Documented skips: the network-marked provider tests are excluded by mandatory CI policy; eight international-slate artifact cases lack downloaded files in CI, but the actual-artifact module passed all ten locally. CI's empty-workspace live engine skips two opt-in data journeys; the real-data runs above exercise both. The prospective workspace intentionally contains no copied historical backtest report. Deployment is skipped by branch policy. Actual future outcomes, official inactive completeness, unknown injury issue times, unresolved official player mappings and authorized TN pricing remain unavailable evidence, never substituted test data. Dependency checks retain existing audit policy exceptions: two moderate production JavaScript advisories, additional development-tool advisories including high/critical findings, and two configured Python advisory exceptions (the audit reports four ignored findings including aliases); passing the configured gate is not a clean dependency audit.

Original populated database SHA-256 remains `133ff5b1a59229280ec19e84c59c314f200b377623b3bfa26de0fa583e2e5d0a`; all three unresolved evaluations remain untouched. Existing frozen studies remain separate. No master modification, deployment, installed update, original database migration, paid-provider activation, model promotion or wagering occurred. NO BET remains enforced. Tennessee market/pricing rights and exact SGP combined offers remain unverified external gates.

## Authorization correction, October 15

Automatic NFL.com retrieval is now blocked at the collector entry point before HTTP-client construction, database reads or poll writes. No environment variable or CLI setting enables collection. The existing parser, fixtures and bounded transport remain; documented prior written authorization and a separate reviewed activation change are required. [Exact external requirement and licensed alternatives](../../docs/release1b4-official-access.md).

[New real-data evidence](disabled-collection-evidence.json) was generated using application code `b7c375649ddf8f6eecb82747886b09b88ec5ea12` and acceptance harness commit `cd78567` (the latter does not change application behavior). The standard worker ran with `--prospective` and no skip flag, observing every HTTPX request including redirect hops. It made **zero NFL.com request attempts**; actual destinations were ESPN (12) and NWS (12). Existing nflverse evidence was still within its refresh interval and was reused. The worker reported `official_disabled_pending_authorization: 1`; its two historical official polls remained unchanged and no new official poll or source was written.

The worker created **42 new forecasts for 14 games** from **4,254 observed results**. Immediate forecast retry: **zero created, 14 unchanged**. Every prior row in the 12 recorded evidence/model/schedule tables passed hash comparison, including all **252 earlier forecasts**; total forecasts are now 294. All 60 pre-existing raw/artifact files passed byte-hash checks. SQLite integrity and foreign keys passed. Official availability remains incomplete for every new forecast's teams; NO BET remains enforced; actual prospective evaluations remain zero. The controlled final-checkpoint regression confirms that disabled collection cannot unlock final publication.

The first acceptance-harness attempt successfully published 42 records and passed preservation/retry checks, then failed while assembling its report because the read-only view call omitted its required `now` argument. That harness defect was corrected; none of its records were removed. The successful run includes those records in its 252-row preservation baseline. The original run log remains local as `disabled-collection-run.log`; the successful log is `disabled-collection-rerun.log`. This is not a model or forecast-generation failure.

Validation after the correction:

- Focused official-collection, prospective, historical-training and workspace-protection suite: **30 passed**. Four new parameterized regression cases cover explicit clients, HTTP-client construction, environment guesses, both compatibility-flag states, unchanged evidence, idempotency and the final-checkpoint gate.
- Lint passed; type check passed all 89 source files.
- Actual prospective API/browser journey: **8 passed, 1 intentional legacy-study skip**. Separate unchanged retained study, opened through a read-only SQLite URI: **9 passed**. The prospective-data opt-in is exercised only by the first run; the study run validates historical comparison.
- Actual-artifact international-slate suite: **10 passed**. An initial invocation pointed at the prospective raw directory, which lacks the legacy `games` dataset name, and skipped eight artifact cases; rerunning against the existing study's raw artifact exercised all ten without new downloads.
- Mandatory backend, PostgreSQL, frontend, browser and live-engine CI links/results for the final PR head are recorded in PR #6. Network-provider exclusions, empty-CI data opt-ins and the existing dependency policy exceptions remain unchanged; no skipped gate is claimed as executed.

The original populated database remains SHA-256 `133ff5b1a59229280ec19e84c59c314f200b377623b3bfa26de0fa583e2e5d0a`; the retained ordered Release 1B.3 study remains `89ef633f244a514a68c216cab54f6ad39bda6d986c94ce4f2d2efeb4feba734e`. Original `forward_ledger` rows 1, 2 and 3 retain their unresolved historical status and unchanged contents. No original migration, study rewrite, merge, deployment, installed update, paid activation or model promotion occurred.

New screenshots preserve the earlier captures: [War Room](disabled-war-room-desktop.png), [Game Lab desktop](disabled-game-lab-desktop.png), [Game Lab mobile](disabled-game-lab-mobile.png). Mobile evidence was visually inspected; NO BET and zero evaluated forecasts remain visible.
