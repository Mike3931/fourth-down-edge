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

Two errors in the earlier Phase 3B report are corrected here rather than
by amending the commit that carried them.

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
`QUOTA_EXHAUSTED`. The mode travels with every captured record. Fixture
output is never stored or described as live-provider output.

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
