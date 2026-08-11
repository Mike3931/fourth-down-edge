# Walk-forward model-integrity certification

Date: 2026-08-03. Branch: `feature/forward-data-capture`.

## 1. The isolated fix

| | |
| --- | --- |
| Commit | `6870a7f1c457ecc9807331bbb2e9668b24bc5194` |
| Parent | `6d5b20bb24d47bc72a45999dc428daf2847b613e` |
| Files | `apps/api/src/fde_api/backtest/walkforward.py`, `apps/api/tests/test_sequential_replay_leak.py`, `docs/model-governance.md` |
| Working tree at the time | clean |

Already isolated — the commit contains the fix, its tests, and its
documentation, and nothing else. No cherry-pick surgery was required.

Code path: `walkforward.sequential_ratings_moments`. Added an invariant
check rejecting `result_observed_at <= kickoff`, and an explicit
`o.id == g.id` skip so the game being predicted is never observed first.

## 2. Result-visibility rule as implemented

```
visible      iff  result_observed_at <  as_of_at
not visible  when result_observed_at == as_of_at
not visible  when result_observed_at >  as_of_at
```

Four layers, none depending on `RESULT_AVAILABILITY_OFFSET`:

0. **Ingest** — `canonical/load_games.py` refuses any row whose
   `result_observed_at` is not strictly after kickoff, or is naive.
1. **Strict visibility** — `ReplayClock.can_see_result` (`<`), distinct
   from the inclusive `can_see` used for observation vintages.
2. **Self-exclusion** — the current game is skipped by id.
3. **Invariant validation** — a malformed row raises `LookaheadError`.

Verified: with `RESULT_AVAILABILITY_OFFSET` monkeypatched to `0` and to
`-1h`, ingest refuses. Timestamps are UTC-aware at the ORM boundary via
`Base.type_annotation_map` mapping `datetime → UtcDateTime()`.

## 3. Simultaneous kickoffs

Tested and **a defect was found**. Predictions for two games sharing a
kickoff were mutually independent, but `observe_game` mutates ratings and
is not commutative, so the order two simultaneous *results* were absorbed
depended on input list order — and every later prediction inherited it.
Reversing the input list changed a later game's prediction.

Fixed by deriving both orders from the data alone: prediction order
`(kickoff, id)`, observation order `(result_observed_at, id)`. The replay
is now a pure function of the game set.

## 4. Historical data scan

`reports/integrity/historical-data-scan.json`
sha256 `b12f4aab2239efb40a55fd67bf6eb1888e43b1352fdabf0e4beed266fa47600f`

| metric | value |
| --- | --- |
| games scanned | 2227 (seasons 2018–2025) |
| completed games | 2227 |
| min `result_observed_at − kickoff` | 16200 s (4h30m) |
| max offset | 16200 s (4h30m) |
| result time == kickoff | 0 |
| result time < kickoff | 0 |
| missing result time | 0 |
| naive after ORM decode | 0 |
| naive after load | 0 |
| duplicate canonical ids | 0 |
| conflicting results | 0 |
| rejected | 0 |
| **required condition holds** | **true** |

`raw_without_tz_designator` is 2227 and is *not* a defect: SQLite has no
timezone type, and `UtcDateTime` supplies UTC on decode. Reported so that
"0 naive" is not misread as a claim about storage.

## 5–6. Rerun and comparison

Both recorded folds (test seasons 2024 and 2025) were rerun from their own
stored configs — same seasons, folds, grids, seeds, execution assumptions,
feature and model versions. No new selection or tuning.

Compared: fold membership, selected hyperparameters, calibration
selection, all 3420 predictions, all 2280 candidate rows (status, line,
price, execution, settlement), all 12 evaluations, 2 calibration
artifacts, 6 model registrations, and both run metric blocks. Float
tolerance `1e-9`.

### Attribution

| variant | differences | snapshot hashes |
| --- | --- | --- |
| lookahead fix alone (`6870a7f`) | **2** | `d2db2490…` → `d2db2490…` (identical) |
| + strict `can_see_result` | **2** | identical |
| + deterministic ordering | **5634** | `d2db2490…` → `f02ebd4f…` |

The 2 differences in the first two variants are the rerun's own generated
`run_id` (`bt_4cfbc3643125` → `bt_0b9cd81a3500`), which is a uuid minted
per run and is not an evaluation result.

The 5634 differences are confined to `team-ratings-v1`, the only
sequential model. The other five models are byte-identical.
**`recommendations` shows zero differences** — no candidate status, no
simulated wager, no ROI, no settlement changed.

Representative metric movement (all *worse*, i.e. the deterministic
version is the less flattering one):

| metric (test 2024) | before | after | delta |
| --- | --- | --- | --- |
| brier | 0.2158905903 | 0.2159002395 | +9.65e-06 |
| log_loss | 0.6214827784 | 0.6215017958 | +1.90e-05 |
| crps_margin | 7.39199905 | 7.39215085 | +1.52e-04 |
| margin_mae | 10.15930608 | 10.15963462 | +3.29e-04 |

Comparison artifact: `reports/integrity/walkforward-prefix-vs-postfix.json`
sha256 `b433e0d6e08fb83f04a89ec40ee5ff4969f196a95bbd55129ea7dbfb29fcbb78`

## 7. Certification

Scoped precisely, because two distinct corrections are involved.

**For the lookahead vulnerability — CERTIFIED:**

> The latent replay vulnerability was not realized in the historical
> datasets used for the published Phase 2 evaluation. The complete frozen
> walk-forward rerun produced identical predictions and evaluation results
> within the documented reproducibility tolerance.

**For the determinism correction — NOT CERTIFIED:**

> The replay correction changed one or more historical predictions or
> evaluation results. Prior reported metrics are superseded and must not
> be cited.

This second statement applies **only** to `team-ratings-v1` predictions
and its evaluation metrics. It does not apply to the other five models, to
any candidate status, or to any simulated wager or ROI figure, all of
which are unchanged and remain citable.

The determinism correction is a reproducibility fix, not a leak fix. The
superseded numbers were not wrong in the sense of using future
information; they were computed under an absorption order that depended on
how rows happened to arrive.

## 8. Temporal-integrity audit

`reports/integrity/temporal-integrity-audit.md` — 19 comparisons
classified. 3 defects corrected (strict result visibility, deterministic
ordering, ingest guard); 2 remain intentionally inclusive with documented
reasons and boundary tests.

## 9. Phase 2 backport — NOT DONE

Not performed in this pass. The isolated commit and its parent are
recorded above and the fix is ready to cherry-pick, but the Phase 2
lineage checkout, full-suite run, report regeneration and
`phase-2-research-engine-v1.0.1` tag have not been executed. Claiming
otherwise would be false.

## 10–11. Dependency versions

| package | before | after |
| --- | --- | --- |
| fastapi | 0.116.2 | **0.141.1** |
| starlette | 0.48.0 | **1.3.1** |
| uvicorn | 0.35.0 | 0.35.0 |
| anyio | 4.14.2 | 4.14.2 |
| httpx | 0.28.1 | 0.28.1 |
| python-multipart | not installed | not installed |

Constraint changed from `fastapi~=0.116.1` to
`fastapi>=0.141.1,<0.142` plus an explicit `starlette>=1.3.1,<2`.
FastAPI 0.141.1 requires only `starlette>=0.46.0` with no upper bound, so
no resolution override was needed and no prerelease was adopted.

Security floor `starlette >= 1.0.1` — **met (1.3.1)**.

Exposure audit: no `StaticFiles`, no `FileResponse`, no `UploadFile` /
`File(...)` / `Form(...)` / multipart route, and no middleware or handler
reads `request.url`, `request.url.path`, or Host-derived values for
authorization, routing, tenant selection, redirects, or security policy.

pip-audit: 10 advisories before → **2 after** (pyarrow `PYSEC-2026-113`,
dev-only pytest `PYSEC-2026-1845`). All six starlette advisories cleared
and removed from the CI ignore-list.

## 12. Security regression tests

`apps/api/tests/test_starlette_security.py` — 25 tests.

Host handling: 9 malformed values including `/`, `?`, `#` combinations —
routing unaffected, authentication not bypassable, Host not reflected.
Multipart and FileResponse: asserted **absent** rather than limit-tested,
because mounting a `StaticFiles` app or adding a form parser purely to
exercise those advisories would create the very surface the audit
confirmed is not there. CORS and OpenAPI generation re-verified.

Trusted hosts: not configured. This service binds to a private research
deployment and the CORS allowlist already constrains browser origins;
adding `TrustedHostMiddleware` is a deployment decision, recorded as
outstanding rather than silently adopted.

## 13. Calibration-selection correction — rerun and comparison

### What changed in the code

Calibrator selection was in-sample: each candidate was fitted on the
validation sample and then scored on that same sample, which ranks methods
by parameter count rather than by generalisation. It now selects by
out-of-fold log loss over a seeded 5-fold partition, refitting the winner
on the whole sample. See `docs/model-governance.md` and
`apps/api/tests/test_calibration_selection.py`.

### Rerun

Both recorded walk-forwards replayed from their own stored configs, so
seasons, grids, seeds and execution assumptions are identical by
construction. Only the code differs.

| | value |
| --- | --- |
| pre-rerun snapshot | `f02ebd4f1c4236af59379a67d83a1d163bc1086a9cf18c51a72cd5dd6452c8fe` |
| post-rerun snapshot | `ec98b68ff3b11a7ebafd18222ae41ebb544dfb17392253aa9e00350849febf50` |
| differences | 8098 |
| artifact | `reports/integrity/walkforward-calibration-selection.json` |
| artifact sha256 | `85876ec721a38fa636492c9baad70d87ee33f93d0a4fd56bf47e62ac6c46bc6d` |

The pre-rerun snapshot equals the post-rerun snapshot of section 5–6, so
the certification chain is continuous.

### The selection flipped, consistently and narrowly

Both folds now choose `none` — the identity — over `beta`:

| fold | none | platt | beta | chosen |
| --- | --- | --- | --- | --- |
| val 2023, n=271 | **0.694120** | 0.697063 | 0.695817 | none |
| val 2024, n=281 | **0.690554** | 0.696359 | 0.692391 | none |

Two things about this table matter more than the winner. The margin is
about 0.002 of log loss, and every value sits within 0.005 of ln 2 =
0.6931 — the score of a constant 0.5 forecast. Spread cover is close to a
coin flip by construction, so the calibration sample carries very little
signal, and "no calibration beats every fitted alternative" is the
unsurprising reading. The direction is at least consistent across two
independent folds.

### Model evaluation metrics are unchanged

Every model's Brier, log loss, CRPS and MAE moved by at most 1e-13, which
is float noise from the `home_win_prob` normalisation (section in
`docs/model-governance.md`). Calibration is applied only to spread-cover
probabilities inside the betting simulation; it never touched the metrics
the Model Audit screen ranks on.

**The model comparison, and every "indistinguishable from the market"
verdict, is unaffected and remains citable.** `team-ratings-v1` retains
the 9.65e-06 determinism delta recorded in section 6, and its
post-correction value remains the current one.

### The betting simulation changed substantially, and for the worse

Applying no calibration stops the probabilities being shrunk, so many more
apparent edges clear the threshold.

| | 2024 before | 2024 after | 2025 before | 2025 after |
| --- | --- | --- | --- | --- |
| bets | 4 | 163 | 104 | 47 |
| ROI per bet | −2.38% | +1.04% | −0.11% | **−17.63%** |
| ROI 90% CI | not computed (n<5) | [−11.79%, +13.99%] | [−15.65%, +15.09%] | [−41.49%, +1.02%] |
| P&L units | −0.095 | +1.693 | −0.111 | **−8.285** |
| max drawdown | 2.0 | 10.74 | 15.97 | 13.15 |

Net across both test seasons: **−0.21 units before, −6.59 units after.**

The corrected run is the less flattering one. The 2024 column in
isolation is not a profitability result and must not be read as one: its
interval spans zero by a wide margin, and the 2025 fold — the more recent
of the two — returns −17.63% per bet over 47 bets with an interval whose
upper bound barely reaches zero.

What the old numbers mostly recorded was suppression. Four bets in a
season is not a strategy that lost slightly; it is a calibrator, selected
because it reproduced its own fitting sample, shrinking almost every edge
below the threshold. Removing it did not reveal skill — it revealed that
the candidate generator produces many apparent edges and that acting on
them lost money over the two seasons available.

### A finding this rerun exposed: the edge threshold is chosen on a noisy criterion

The candidate edge threshold is selected by maximising simulated ROI over
a grid on the validation season, guarded only by `n_bets >= 20`. Validation
ROI over a few dozen bets is extremely noisy, and the rerun shows it
failing to carry:

| fold | chosen threshold | validation ROI | test ROI |
| --- | --- | --- | --- |
| 2024 | 0.03 | +11.03% | +1.04% |
| 2025 | 0.08 | +1.31% | −17.63% |

This is not leakage — the threshold is chosen on validation and measured
on test, which is the correct structure. It is a selection made on a
statistic too noisy to support it, and a reader should treat the test
figures above as the outcome of a threshold picked essentially at random
within its grid. Recorded here rather than changed: altering the selection
criterion is a modelling decision, not a defect fix.

### Where the regenerated state actually lives

`apps/api/data/*.db` is gitignored, so the reran rows — the new
`BacktestRun`s, the two `cal_none_*` artifacts, the third evaluation per
model — exist in the working database on the machine that ran the
certification, not in the repository. What the repository carries is the
corrected code, this record, the comparison artifact, and the regenerated
`apps/api/data/reports/phase2_*`, which are tracked and were rebuilt from
the post-rerun database.

A checkout elsewhere therefore has code that will select `none` on the
next run, alongside whatever database that machine already had. Anyone
reproducing these figures must rerun the certification locally; the
numbers above are not recoverable from a fresh clone alone.

### Certification

**For the calibration-selection correction — NOT CERTIFIED as unchanged:**

> The correction changed the recorded betting simulation. Prior
> betting-simulation figures — bet counts, ROI, P&L and drawdown, for both
> test seasons — are superseded and must not be cited.

**Model evaluation metrics — unchanged and citable**, as set out above.

Neither the superseded figures nor the current ones support any claim of
profitability, predictive superiority, or readiness for real money. Both
test seasons remain burned for evaluation purposes under section 6 of
`docs/model-governance.md`.
