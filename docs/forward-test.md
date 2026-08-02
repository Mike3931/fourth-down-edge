# Forward test — methodology and operations (Phase 3)

> **LIVE RESEARCH MODE — FORWARD MODEL NOT APPROVED FOR REAL-MONEY DECISIONS**
>
> Nothing in this phase produces a betting recommendation. Statuses are
> `RESEARCH_CANDIDATE`, `WATCH`, `PASS`, `DATA_INCOMPLETE`. No `BET`
> state exists in the engine, and every model artifact remains
> `research_only`.

## Why forward capture

The 2024 and 2025 seasons were inspected during Phase 2 and are burned as
test sets (see [model-governance.md](model-governance.md)). An honest
out-of-sample claim now requires data that did not exist when the model
was frozen. That is the entire purpose of this phase: capture what is
knowable at each moment, before outcomes are known, and let the 2026
season judge the model.

## 1. The frozen policy

Everything that could otherwise be decided after seeing results is fixed
in advance and content-hashed.

* **Policy version:** `ftp-2026-v1`
* **Policy hash:** `2128907c078df12a34dd2dfabf12887d23516190f27cc6e6d56c63bcd921d199`
* **Window:** 2026-09-01 → 2027-02-28
* **Model:** `market-residual-v1` · **Features:** `nfl-core-v1`
* **Research-candidate threshold:** 0.05 edge, 0.015 watch band
* **Uncertainty haircut:** 0.02 applied before comparing to break-even
* **Minimum data completeness:** 0.80

Changing *any* field produces a different hash, which requires a new
policy version and starts a **separate evaluation cohort**. Attempting to
modify a frozen policy in place raises `PolicyImmutabilityError`. The
thresholds were carried over unchanged from Phase 2 and deliberately not
re-tuned, because tuning them against the inspected seasons would
contaminate the forward test before it began.

## 2. Prediction vintages

| Horizon | Cutoff before kickoff |
| --- | --- |
| `OPENING` | 6 days |
| `EARLY_WEEK` | 4 days |
| `PRACTICE_UPDATE` | 2 days |
| `FINAL_INJURY_REPORT` | 1 day |
| `PREGAME` | 90 minutes |
| `CLOSING_CAPTURE_EVALUATION_ONLY` | kickoff (evaluation only) |

Vintages are immutable and content-hashed. Regenerating one with
different inputs raises `VintageImmutabilityError`; regenerating with
identical inputs returns the existing row. A vintage cannot be generated
before its cutoff has actually arrived. The closing capture is stored
like any other vintage but is excluded from every prediction input path.

Each vintage records full lineage: consensus snapshot ids, weather
vintage id, injury observation ids, schedule observation id, roof state,
and QB resolution — so any number can be traced to the exact source
records that produced it.

## 3. Market capture and consensus

Provider adapter targets **The Odds API**, selected by
`FDE_ODDS_API_KEY` (backend only; never in a browser bundle). Missing
credentials raise `OddsProviderNotConfigured` rather than returning
nothing, so "no key" can never be mistaken for "no prices".

Default research cadence (configurable, quota-enforced):

| Time to kickoff | Interval |
| --- | --- |
| > 7 days | 6 hours |
| 7 days – 24 h | 1 hour |
| 24 h – 6 h | 15 minutes |
| 6 h – 90 min | 5 minutes |
| final 90 min | 2 minutes (quota permitting) |
| final 10 min | closing capture |

Quotes are stored exactly as received, with the provider timestamp and
our local observation instant. Nothing is interpolated across gaps or
suspended markets.

**Consensus (`consensus-v1`)** is additive — it never replaces raw
quotes. Eligibility requires: pregame, recognized sportsbook, within the
freshness threshold, observed at or before the cutoff, valid price and
line, and one quote per book (newest wins). Rejections are counted by
reason. Each snapshot records median line, price at the median line,
no-vig probability, eligible book count, line and price dispersion,
oldest/newest quote age, and the ids of every contributing quote.

Prices are only ever compared **at the median line** — averaging prices
across different lines would invent a quote nobody offered.

## 4. Closing line and CLV

The close is selected **by rule, fixed before capture**: the last
eligible consensus snapshot at or before kickoff and within 30 minutes of
it. If no snapshot falls in that window, CLV is reported as unavailable
for that game — never substituted.

* **Spread / total:** compare the simulated accepted line against the
  closing consensus line. Line CLV is reported in points. When the lines
  are equal, CLV falls back to the no-vig price difference. The two are
  reported **separately** and never blended.
* **Moneyline:** compare no-vig implied probability at simulated
  execution against the no-vig probability of the predetermined close.

## 5. Weather

NWS adapter resolves stadium coordinates → office/grid → forecast
endpoint, preserves the raw response, and records the NWS issuance time
alongside our observation instant. Every vintage is retained; an
unchanged forecast writes no new row.

International venues raise `VenueNotSupported` and are recorded as such —
NWS covers US locations only, and the failure is explicit rather than a
silent gap. Fixed domes are `weather_applicable = false`. Retractable
roofs remain weather-relevant because the roof state is itself uncertain.

**Roof state** is never inferred from the result or a postgame report.
An unobserved retractable roof stays `UNKNOWN`, which the policy converts
to `DATA_INCOMPLETE`.

### Known venue trap (fixed)

The schedule source populates `stadium_id` and `roof` with the **home
club's** stadium even for games played abroad. `2026_01_SF_LA` at
Melbourne Cricket Ground carries `LAX01`/"dome". Trusting those columns
would query Los Angeles weather for a game in Australia and mislabel an
open-air ground as a dome. `resolve_venue()` therefore ignores
`stadium_id` entirely for neutral-site games and resolves by venue name.

## 6. Injuries and availability v0

No restricted site is scraped. Observations arrive through structured
manual entry or a licensed provider, each carrying source category,
observation instant, and verification status.

Source categories: `OFFICIAL_VERIFIED`, `LICENSED_PROVIDER`,
`USER_VERIFIED`, `UNVERIFIED`. **Unverified reports never become
production features** — they may be displayed in a research context only.

An observation dated in the future is rejected with `LookaheadError`.
Revisions supersede rather than overwrite, so the original designation
survives.

**Availability v0** is a rules layer producing *ranges*, not point
estimates, because no forward sample yet justifies a fitted model:

| State | Active probability |
| --- | --- |
| `EXPECTED_ACTIVE` | 0.90 – 0.99 |
| `ACTIVE_WITH_RESTRICTION_RISK` | 0.75 – 0.95 |
| `GAME_TIME_DECISION` | 0.40 – 0.70 |
| `DOUBTFUL` | 0.05 – 0.30 |
| `EXPECTED_INACTIVE` | 0.00 – 0.10 |
| `CONFIRMED_INACTIVE` | 0.00 |
| `UNKNOWN` | 0.00 – 1.00 |

Quarterbacks are handled separately. There is **no universal "QB equals
X points" rule**. When the starter cannot be resolved — doubtful,
game-time decision, inactive, or simply underivable — the policy returns
`DATA_INCOMPLETE` rather than guessing. Weighted starter scenarios are a
documented policy option, not a silent default.

## 7. The forward ledger

A distinct immutable cohort, never combined with historical backtests in
any query, metric, or report. It retains **every** state including `PASS`
and `DATA_INCOMPLETE` — a forward test that quietly drops the
opportunities it declined cannot be audited.

Each row records the qualifying price and its source and age, the model
probability, the conservative probability after the haircut, break-even,
expected value, simulated execution (delay, deterioration, fill or
no-fill), the rule-selected close, both CLV measures, result, P/L,
data completeness, and any exclusion reason.

## 8. Honest comparison hierarchy

Forward results are always reported against: naive baseline, the
captured market-implied probability, the frozen analytical model, and the
market-residual model. Primary evidence is **calibration, proper scoring
rules, CLV, stability, and drawdown** — never win rate or short-run ROI.
Every summary carries its sample size and a small-sample warning below
100 settled rows.

## 9. Operating modes

| Mode | Banner |
| --- | --- |
| `DEMO` | DEMONSTRATION DATA — NOT FOR REAL-WORLD DECISIONS |
| `LIVE_RESEARCH` | LIVE RESEARCH MODE — FORWARD MODEL NOT APPROVED FOR REAL-MONEY DECISIONS |

Every forward record carries `data_mode`. `assert_single_mode()` raises
`ModeMixingError` at any aggregation boundary where demo and live records
would combine. There is no fallback from live to demo: absent live data
yields `DATA_INCOMPLETE`.

## 10. Operations

```bash
# freeze the policy (idempotent)
uv run python -c "from datetime import date; from fde_api.db.engine import session_scope; \
from fde_api.forward.policy import build_default_policy, freeze_policy; \
p=build_default_policy(policy_version='ftp-2026-v1', start=date(2026,9,1), end=date(2027,2,28)); \
s=session_scope(); [freeze_policy(x,p) for x in [s.__enter__()]]"

# refresh the schedule (append-only observations)
uv run python -m fde_api.cli refresh-schedule --season 2026   # see runbook
```

### Provider-outage runbook

1. An outage is recorded as a failed job run with its error summary; it
   never writes a partial or fabricated quote.
2. Missing consensus at a cutoff yields `DATA_INCOMPLETE` for that
   vintage — the vintage is still written, with the reason.
3. If the outage spans the closing window, CLV for affected games is
   reported unavailable and the count is published in the report.
4. Do not backfill prices after an outage. A quote observed later cannot
   represent what was available earlier.

### Schema-drift runbook

Provider payload changes surface as `unmapped_events`, `invalid_skipped`,
or unmapped stadium ids. All are counted and surfaced rather than
silently dropped. Add the mapping, then re-run — capture is idempotent,
so replaying a payload writes only genuinely new records.

## 11. Security

* Provider keys are read from backend environment only and never appear
  in a browser bundle.
* bet365 is never contacted, scraped, automated, or credential-stored.
  The user's executable price arrives solely through manual entry.
* CORS origins are environment-configured.
* The software has no capability to place a wager anywhere.

## 12. Known limitations

* **No odds provider key is configured in this environment**, so live
  market capture has not been exercised against the real provider. The
  adapter, consensus, cadence, and quota accounting are verified against
  deterministic fixtures only.
* NWS forecasts extend roughly 7 days; games further out have no weather
  vintage by nature, not by failure.
* Historical injury data remain unavailable; the forward workflow starts
  accumulating from first capture and will be sparse early.
* The 2026 postseason is not yet published by the schedule source; only
  272 regular-season games exist today.
* No forward sample exists yet. Every metric surface is built and tested,
  but until games are played there is nothing to report — and nothing
  about the model's quality can be claimed.

---

## Checkpoint corrections (recorded, not rewritten)

Errors in earlier reports and documentation are corrected here rather
than by amending the commits that carried them.

**1. File count.** The commit `95db21e` report said "four files changed"
while naming five. The correct count is **five**: `venues.py`,
`schedule.py`, `quota.py`, `test_international_slate.py`, and
`test_quota.py`.

**2. Closing-capture language.** The earlier wording said closing
captures are protected "unconditionally". That overstated the guarantee.
The accurate statement, which now appears in `quota.budget_report()` and
is asserted by a test:

> Closing captures receive the highest scheduling and quota priority,
> with reserved credits, but remain subject to provider availability,
> connectivity, rate limits, and remaining subscription credits.

**3. International slate.** The Phase 3 report claimed eight
international games. The published 2026 schedule has **nine games across
eight stadiums** (Tottenham hosts two). The miscount came from detecting
international games via the source's `location` flag, which reads "Home"
for a club's designated home game played abroad. Detection is now
venue-driven. See the regression tests in `test_international_slate.py`.

**4. Provider-mode storage.** This document previously stated that the
provider mode "travels with every captured record". It did not. Neither
`odds_quotes` nor `consensus_snapshots` had a `provider_mode` column, so
a fixture payload and a live provider response produced byte-identical
rows and nothing downstream could tell them apart. The claim was
aspirational, not implemented.

It is implemented now — see **Provider modes** above — but the earlier
statement was wrong when written, and any record captured before
migration `d41a9c72b8e5` carries `UNKNOWN_LEGACY` and cannot retroactively
support a provenance claim in either direction.

## Cohorts

| Cohort | Purpose | Enters official evaluation |
| --- | --- | --- |
| `fixture` | Deterministic test payloads | Never |
| `demo` | Synthetic demonstration data | Never |
| `burn_in` | Operational validation against the real provider | Never |
| `official_forward_test` | The 2026 forward test under `ftp-2026-v1` | Yes |

Cohort membership is immutable. Cross-cohort aggregation requires an
explicit administrative call that demands an operator and a reason and
returns a warning with the figure. Operational defects found during
burn-in may be fixed; **model logic, parameters, calibration,
thresholds, and selection rules may not be changed based on burn-in
outcomes.**

## Provider modes

`FIXTURE`, `SANDBOX`, `LIVE`, `UNAVAILABLE`, `KEY_MISSING`,
`QUOTA_EXHAUSTED`, plus two that describe a conclusion rather than an
origin: `MIXED` and `UNKNOWN_LEGACY`.

Fixture output is never stored or described as live-provider output.

### Where provenance is stored

`odds_quotes.provider_mode` and `consensus_snapshots.provider_mode`, both
indexed and both constrained to the vocabulary above by a database CHECK.

A fixture payload and a live provider response are structurally identical.
Only the caller knows which it holds, so `capture_odds` takes
`provider_mode` as a **required** argument with no default — a default of
`LIVE` would silently mislabel every caller that forgot, which is the
exact failure the column exists to prevent.

### How derived records inherit it

`combine_provider_modes()` decides the provenance of anything built from
several sources:

| inputs | result | why |
| --- | --- | --- |
| all one mode | that mode | nothing to reconcile |
| any `UNKNOWN_LEGACY` | `UNKNOWN_LEGACY` | one unrecorded input makes the whole conclusion unrecorded |
| otherwise differing | `MIXED` | a derived record is only as live as its least-live input |
| none | `UNKNOWN_LEGACY` | absence of evidence about provenance is not evidence of live provenance |

So one fixture quote in a consensus makes that consensus fixture-derived.
Anything more generous would let test payloads launder themselves into
records that read as live market data.

`MIXED` and `UNKNOWN_LEGACY` are refused by `assert_capturable()` on the
write path: they are conclusions, not origins, and capturing one directly
would mean a caller invented provenance rather than observing it.

### Health checks

| check | severity | gates candidates |
| --- | --- | --- |
| `provenance_live_claim_without_live_provider` | CRITICAL | yes |
| `provenance_unrecorded` | WARNING | no |
| `provenance_non_live_in_live_research` | WARNING | no |

The first is the alarming one: rows claim `LIVE` while the service is
running on fixtures, meaning a test payload was captured as live market
data.

The third is deliberately **not** a gate. Burn-in exists to run the
live-research pipeline on fixture payloads, so suppressing candidates
there would defeat the cohort's purpose. It is also not that function's
call to make — `run_health_checks` receives `data_mode` and
`provider_mode` but never the cohort, so it cannot distinguish burn-in
from official. It reports the mixture and leaves the gate to whoever
knows the cohort.

### Migration

`d41a9c72b8e5` adds both columns with `server_default='UNKNOWN_LEGACY'`.
Pre-existing rows are backfilled to `UNKNOWN_LEGACY`, not `LIVE`: their
true provenance is genuinely unknown, and guessing `LIVE` would
manufacture exactly the false claim the column exists to prevent. This
mirrors the `UNKNOWN_LEGACY` treatment already used for scheduler domain
state.

The migration uses `batch_alter_table` because SQLite cannot add a CHECK
constraint in place. The downgrade is structurally supported but
semantically lossy in the same way as the scheduler-state downgrade:
dropping the column discards the provenance of every record captured after
the upgrade, and a re-upgrade backfills them all to `UNKNOWN_LEGACY`.

## Scheduler

Thirteen jobs with cadence declared once in `scheduler.JOB_CADENCE`, from
which the quota forecast is generated — a test asserts the two cannot
drift apart.

Idempotency and locking are the same operation: each run derives a
deterministic key from (job, cohort, logical slot), and that key is
UNIQUE in the database, so a retry, a second worker, or a restart
mid-flight loses the insert race and is skipped rather than duplicating
captures, vintages, or settlements. Failed attempts are retained as
history rather than overwritten.

Catch-up policy is per job. A missed closing capture or odds poll is
`SKIP` — replaying it later would record a price that was never
observable at that moment. Vintages, settlements, and result ingestion
are `RUN_ALL` because they remain correct when computed late.

---

## Scheduler state model (authoritative)

Two independent persisted axes:

| Field | Meaning |
| --- | --- |
| `job_outcome` | How the EXECUTION went |
| `domain_state` | What the DATA looked like |

`status` is **compatibility-only**. New scheduler logic reads
`job_outcome` and `domain_state`; it never derives authoritative state
from `status`, and `domain_state` is never reconstructed from `status`
for new records. Migration code is the only place permitted to
reconstruct the new axes from historical values.

### `UNKNOWN_LEGACY`

A legacy execution status establishes the OUTCOME exactly and says
nothing about the DATA. "finished" means the job ran, not that the domain
was complete. Rows migrated without recorded domain metadata therefore
carry `domain_state = UNKNOWN_LEGACY`, and the audit marks the mapping
**not exact**.

`UNKNOWN_LEGACY` is migration-only. `HandlerResult.__post_init__` raises
`IllegalDomainStateError` if live code attempts to emit it, and
`LIVE_DOMAIN_STATES` excludes it. Completeness metrics report migrated
unknowns as their own bucket rather than folding them into complete or
incomplete; reliability metrics use `job_outcome`, which is always
populated.

### Final legacy mapping

| Legacy `status` | `job_outcome` | `domain_state` | Exact |
| --- | --- | --- | :--: |
| `finished` | SUCCESS | UNKNOWN_LEGACY | no |
| `running` / `queued` | RUNNING | UNKNOWN_LEGACY | no |
| `failed` | RETRYABLE_FAILURE | UNKNOWN_LEGACY | no |
| `dead_letter` | TERMINAL_FAILURE | UNKNOWN_LEGACY | no |
| `interrupted` | INTERRUPTED | UNKNOWN_LEGACY | no |
| `skipped` | SKIPPED | UNKNOWN_LEGACY | no |
| legacy `DATA_INCOMPLETE` | SUCCESS_WITH_WARNINGS | DATA_INCOMPLETE | **yes** |
| legacy `SUPPRESSED` | SUCCESS_WITH_WARNINGS | SUPPRESSED | **yes** |

When the preserved `error_summary` contains an explicit
`domain_state=<value>`, that value is used and the reconstruction is
marked exact.

## Downgrade support decision

> **Downgrade is structurally supported but semantically lossy.**

Downgrading across revision `c034f7ebc265` drops both authoritative
columns. Rows survive and remain queryable, but `domain_state` cannot be
represented by the legacy `status` column — the two are orthogonal, so
the information is destroyed rather than compressed.

Proven, not assumed: a test inserts post-upgrade rows spanning six
outcome/domain combinations, downgrades, re-upgrades, and asserts that
every distinct domain state returns as `UNKNOWN_LEGACY`. The execution
axis *is* recoverable, because `status` projects it.

Do not downgrade a database carrying forward-test records you intend to
analyze. Restore from backup instead.

## Migration audit

`migration_audit` carries `migration_cycle` and a unique identity of
(revision, cycle, table, record), so repeated upgrade cycles can never
produce indistinguishable rows. A downgrade removes the revision's audit
rows, so a re-upgrade writes a clean set. Exact and conservative mappings
remain distinguishable across cycles. The table holds no payload content.
