# Release 1B.3 evidence

The final genuine-data evaluation executed implementation commit `4ab60d0dafa973edf57c76d9e3f294118ac440c7` in a new isolated workspace. Subsequent acceptance changes add tests, documentation and these artifacts. The original profile/database was not used by the app, collector or evaluator.

- [Automated readable comparison](model-comparison.md)
- [Full comparison JSON](model-comparison.json): all folds, metrics, reliability bins, uncertainty, sensitivity and failed models
- [Read-only integrity/registry evidence](integrity-readback.json): 13 research-only model versions, 3,293 predictions, 12 fold evaluations; implementation/source hashes
- [Exploratory comparison 1](exploratory-comparison-1.json) and [comparison 2](exploratory-comparison-2.json): retained development outputs, not independent confirmations or final acceptance
- [War Room model panel](model-war-room-desktop.png)
- [Game Lab model panel, desktop](model-game-lab-desktop.png)
- [Game Lab model panel, mobile](model-game-lab-mobile.png)

Final source bytes SHA-256: `85840e8d6a7299ea2a2f8f6cc19e6a7b6a8492c63b3396f877f020ede6ae9fd2`. Full raw bytes/manifests and serialized artifacts remain in the isolated `research_1b3_frozen_20261014.research-data` directory; no populated database is committed. Scores-only retrospective result times are approximated; do not treat these studies as archived historical vintages or untouched tests. The two exploratory runs predate the implementation commit and remain explicitly exploratory.

Actual fresh capture has 272 unique canonical current-season fixtures, 366 append-only schedule observations (including final/status revisions), zero game-identity review conflicts, seven research source vintages, 17,289 facts and 64,129 player-provider bindings. GSIS provider IDs and canonical GSIS identities are one-to-one. The newer public catalog adds one GSIS and one ESPN binding relative to the morning capture; earlier evidence is retained in its own workspace. Final statuses: home-field reference RESEARCH ONLY; rolling and ratings VALIDATION FAILED against the closing-market screen. No model is eligible for production recommendation status.

Local populated engine/browser acceptance passed all eight cases, including the public-capture and actual-model-study cases that are separately opt-in in deterministic CI. Screenshot text is captured from real stored results, not contract fixtures. Full CI acceptance, all skips and remaining limitations are recorded in the PR. No betting recommendation or exact bet365 quote was verified.
