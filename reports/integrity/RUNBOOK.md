# Running Fourth Down Edge

Everything below is operational. Nothing here claims the models are good,
profitable, or ready for real money, and nothing here should be read that
way. Paper mode is mandatory; no BET state is reachable.

---

## The one command that answers "can we run"

```bash
python apps/api/scripts/operational_readiness.py
```

It separates three questions that get conflated, because each has a
different fix and answering only the loudest one wastes an evening:

| question | what a "no" means |
|---|---|
| Is the GAME in the slate? | it was never ingested; nothing downstream will say so plainly |
| Is the POLICY window open? | there is no evaluation cohort to record into |
| Can market data be CAPTURED? | no key, so no consensus and no price to evaluate |

It reads only — captures nothing, evaluates nothing, writes no record. It
reports whether `FDE_ODDS_API_KEY` is *present* and never its value, not
even a prefix or a hash.

Point it at any instant:

```bash
python apps/api/scripts/operational_readiness.py --at 2026-09-10T00:20:00Z
```

---

## What "ready to pilot" means here, and where it stands

A pilot is the forward test running for real: capture on a cadence,
evaluation into a frozen cohort, and a record afterwards that can be
audited. Four things have to be true, and only one of them is still open.

| | state |
|---|---|
| **1. The slate is ingested and every game resolves to a governed venue** | **done** — 286 games, `unmapped_venues` OK |
| **2. Every non-market input captures** | **done** — schedule, weather (11 NWS vintages), result ingestion all exercised against real data |
| **3. A policy is frozen, hashed, and pins every version it applies** | **done** — `ftp-2026-v2`, calibration `cal_none_val2024` pinned, window opens 2026-09-01 |
| **4. Market data supports a consensus** | **BLOCKED — needs `FDE_ODDS_API_KEY`** |

Point 4 is not work. ESPN gives one book, a consensus needs three, so
every market reads DATA INCOMPLETE and **no research candidate can be
produced at all**. That is the consensus layer refusing to invent
agreement among books it never consulted, and no amount of engineering
changes it. A multi-book provider is a purchase.

Everything else that is red today clears by doing the thing, not by
fixing anything:

* `scheduler_running` — clears on the first run, which cannot happen
  before 2026-09-01 because the scheduler refuses outside the policy
  window. Its long-run behaviour is covered by
  `tests/test_scheduler_long_run.py` in the meantime.
* `odds_key_configured`, `odds_freshness`, `consensus_availability` —
  all four wait on the key.
* `provider_quota` — reads `UNKNOWN_PLAN` until one real provider
  response records the plan from its own headers.
* `prediction_vintage_coverage` — clears once the scheduler runs
  `prediction_vintage`.
* `injury_freshness` — no injury source is wired; entries are manual.
* `provenance_unrecorded` / `provenance_non_live_in_live_research` — 33
  historical rows that predate provenance capture, in `LIVE_RESEARCH`.
  Excluding or re-capturing them is a decision, not a defect.

**Nothing in a suppressing scope is failing, today or projected to Week 1.**
So the gate is open and the engine evaluates; what it finds is DATA
INCOMPLETE, for the reason above.

## Where things actually stand

| | now (2026-08-12) | Week 1 (2026-09-10) |
|---|---|---|
| verdict | **PARTIAL** | **PARTIAL** |
| game within 12h | no — next is 2026-08-14 | yes |
| policy window | closed until 2026-09-01 | open (`ftp-2026-v1`) |
| `FDE_ODDS_API_KEY` | not set | not set |

The slate holds **283 games** — 272 `REG` from 2026-09-10 to 2027-01-10,
plus 11 `PRE` captured from ESPN in August.

The "now" verdict was **BLOCKED** when this table was written, and the
change is a correction rather than progress. The verdict was decided by
counting blockers, and one of the three was "no game kicks off within 12
hours", which blocks nothing on a Tuesday — the slate covers the date and
the next kickoff is simply later. It is now reported under WORTH KNOWING
and the verdict is derived from what can still run. Schedule, weather and
injury capture, data health and the record-chain audit all run today, and
a verdict of BLOCKED printed directly above that list was the report
contradicting itself.

**At Week 1 the only remaining blocker is the provider key.** Everything
else is in place.

### Why tonight cannot be a real test

Two mechanical blockers, and one that no health check would report:

The models are **regular-season models**. `nfl-core-v1` features are built
from prior regular-season games and calibration was fitted on
regular-season outcomes. Preseason is different football — starters play a
series, rosters are ninety deep, and the result is close to noise relative
to team strength. A preseason game pushed through would produce a number
that looks authoritative and means nothing. That is the failure this whole
system exists to refuse.

---

## What you CAN run tonight

### Capture real market prices, with no provider key

```bash
python apps/api/scripts/espn_live_capture.py --dry-run --dates 20260814-20260818
python apps/api/scripts/espn_live_capture.py --dates 20260814-20260818
```

This is the command that has produced every real quote in the database,
and it was missing from this runbook. ESPN's public scoreboard needs no
credential and carries both the fixture and sportsbook lines, so it works
today while `FDE_ODDS_API_KEY` is unset.

Every row it writes carries `provider="espn"`, so it can never be confused
in the record with The Odds API. bet365 is not contacted, scraped or
inspected, and nothing it writes implies a price is currently available to
transact.

It will not manufacture a consensus. ESPN typically exposes one book, a
consensus needs three, and the honest output of a one-book slate is
DATA INCOMPLETE — which is what the Live Slate shows, with the reason
stated. Re-running is idempotent: an unchanged price is a duplicate and is
skipped, so the quote history records movement rather than polling.

`--dates` is **UTC**, like every other date in this system. ESPN's own
scoreboard dates are US Eastern, so the query is widened by a day on each
side and the result filtered back to the UTC window asked for. Without
that, `--dates 20260807` returned nothing for a game whose kickoff and
canonical id both say the 7th — and this exact five-day window returned
**10 games instead of 13**, silently missing ARI @ LV, LAC @ HOU and
TEN @ SF, all kicking off at or just after midnight UTC and therefore
filed by ESPN under the previous Eastern evening. In the regular season
that is every Sunday-night, Monday-night and late-Sunday game.

Omitting `--dates` gives ESPN's default view, which in mid-August is the
finished Hall of Fame game and nothing else. Pass the window you want.

Latest run (2026-08-12 12:2xZ): 13 preseason games, 134 ESPN quotes in the
database, one book throughout. Re-running writes nothing when no price has
moved — a second run minutes later wrote 0 and skipped 60 as duplicates.

To keep capturing, in a terminal you can stop:

```powershell
while ($true) {
  uv run --project apps/api python apps/api/scripts/espn_live_capture.py --dates 20260814-20260818
  Start-Sleep -Seconds 900
}
```

Deliberately not a scheduled task or a service. Nothing persistent is
created, capture stops when the window closes, and the quote history
records only what was genuinely observed while it ran.

**What one book means, stated once so it is not rediscovered as a bug.**
ESPN exposes a single book. `build_consensus` requires three, so it
returns nothing and the market is DATA INCOMPLETE — correctly. No
consensus means no price to compare a model probability against, which
means **no research candidate can be produced from this data at all**, and
the forward-test cohort stays where it is. `odds_key_configured` stays
CRITICAL for the same reason.

None of that is a fault to fix. It is the platform declining to invent
agreement among books it never consulted, which is the behaviour the whole
consensus layer exists to guarantee. Capturing anyway is still worth it:
the quotes are a real, timestamped record of one book's line moving toward
kickoff, and that record cannot be reconstructed later.

### The full lifecycle, end to end

```bash
python apps/api/scripts/rehearsal.py
```

Drives one game through every stage in order — schedule, odds, consensus,
weather, injuries, availability, features, prediction, price observation,
evaluation, closing capture, result, settlement, CLV, chain reconciliation
— through the real scheduler and the real handlers, with nothing stubbed
but the provider.

Fixture prices in a DEMO cohort. **Not live-provider output, and it must
never be described as such.**

It writes to a throwaway database and leaves the configured one alone.
Latest run: all 15 stages finished, 274 schedule observations, 36 quotes,
6 consensus snapshots, 49 predictions, 2 ledger entries (one filled
research candidate settled WIN at +0.5 line CLV, one PASS not executed),
chain verdict `CONSISTENT`.

### See what the scheduler would do

```bash
python apps/api/scripts/run_scheduler.py --once --dry-run
```

A dry run writes nothing and is deliberately **not** gated on the
governance preflight — being unable to look at what is due, because a real
run would have been refused, only teaches people to pass override flags by
reflex. The refusals are still printed.

---

## Running for real

```bash
python apps/api/scripts/run_scheduler.py --once
python apps/api/scripts/run_scheduler.py --interval 60
```

It refuses three things, each guarding a way a run could quietly record
something untrue:

* **a closed policy window** — captured records would belong to no
  evaluation cohort
* **FIXTURE data inside LIVE_RESEARCH** — fixture output must never be
  stored as live-provider output, and a default nobody looked at is the
  easiest way for that to happen
* **LIVE provider mode with no key** — so that "no key" is never mistaken
  for "no prices"

Overrides exist (`--allow-closed-window`,
`--allow-fixture-in-live-research`) and both have to be typed, so nobody
later mistakes a rehearsal for forward-test evidence.

`SIGINT`/`SIGTERM` finish the current job and stop. Startup runs
reconciliation first, which is what turns an interrupted previous run into
a recorded, explainable gap rather than a silent one.

**It has never actually run here.** `scheduled_job_runs` is empty and
`scheduler_running` has been CRITICAL since the check existed, because the
scheduler refuses outside the policy window and that window opens on
2026-09-01. So the first real run is also the first test of a long run,
on the day it starts mattering.

`tests/test_scheduler_long_run.py` covers what that day would otherwise
discover: two hundred consecutive slots, one tick each, asserting the
properties that hold at three slots whether or not the code is right and
stop holding at two hundred if it is not — run rows growing linearly with
slots rather than faster, a full replay of the history adding nothing,
idempotency keys staying inside `VARCHAR(160)` and never colliding, the
retry chain per failing slot staying bounded, and no CRITICAL lineage
violation across fifty consecutively failing slots.

It also pins the credit accounting at length, which is where this codebase
has already been bitten once: `credits_used_since` SUMS `calls_used`, so a
cumulative counter written into that column reports 1+2+…+n instead of n.
At two hundred polls that is 20,100 against 200.

What the test cannot cover is a process staying up for six months. That is
an operational question — a service, a supervisor, or a person watching
`scheduler_heartbeat`, which goes DEGRADED after two hours of silence.

### Going live at Week 1

Set the key either way. Never pass it to this tooling as an argument, and
never put it in a file that gets committed.

```bash
export FDE_ODDS_API_KEY=...
```

or create `apps/api/.env` (already gitignored) containing:

```
FDE_ODDS_API_KEY=...
```

Both reach the provider adapter. The `.env` route did not work until the
settings model declared the field — it ignores extra keys, so the file was
read and the key silently dropped, and the health check then told the
operator to set a key they had already set.

Then:

```bash
python apps/api/scripts/run_scheduler.py --once --dry-run --provider-mode LIVE --data-mode LIVE_RESEARCH
```

and drop `--dry-run` when the plan looks right.

---

## Provider budget — decide this before September

The scheduler's own cadence costs **6,733 credits a month** and **21,546
for the regular season**, derived from the cadence table rather than a
hand-maintained constant.

The account's last recorded balance was 496 credits — recorded with no
plan beside it, so the plan itself is **unknown**. If it is a 500-credit
plan that buys **166 polls a month, about 5.5 a day**, roughly a **13×
shortfall** against the configured cadence. If it is a 20,000-credit plan
nearly spent, the answer is different. Nothing in the record distinguishes
them, which is the point of the state below.

This is a subscription decision, not a bug. What the scheduler does about
it is deliberate: when credits are constrained it sheds cadence on distant
games first and preserves near-kickoff polling, because a stale six-day-out
price costs nothing while a missing close destroys CLV for that game
permanently. Closing captures are protected until the credits are actually
gone.

Thresholds scale to whatever plan the provider's headers reveal —
`x-requests-remaining` + `x-requests-used`, which the provider sends on
every response. Until one real response lands the plan is unknown, and
that is its own state (`UNKNOWN_PLAN`) rather than a fallback to the
configured absolutes.

It has to be. The absolutes assume a 20,000-credit plan, which made the
496-credit balance CRITICAL, and `authorize_poll` then declined every poll
outside two hours of kickoff — including the poll whose response would
have recorded the plan. **The system would not make the call that would
tell it the thing it was refusing over.** `UNKNOWN_PLAN` permits polling
at reduced cadence against a 60-credit daily probe budget, which is enough
to learn the plan many times over and below any plausible month.

So the first thing to expect after setting the key is a poll, a recorded
plan, and this section becoming answerable.

---

## Reading the health checks

40 checks, grouped by scope so an operational problem cannot be mistaken
for a decision-integrity one:

| scope | means |
|---|---|
| `DECISION_INPUT` | affects whether a decision can honestly be made |
| `OPERATIONAL_PLATFORM` | the machinery, not the analysis |
| `POSTGAME_EVALUATION` | affects what can be measured afterwards |
| `GOVERNANCE_INTEGRITY` | affects whether the record can be trusted |

A check gates candidate generation when its severity is CRITICAL **and**
its scope may suppress (`DECISION_INPUT` or `GOVERNANCE_INTEGRITY`). Both
halves are required — see `docs/forward-test.md`. So the two CRITICAL
operational checks below are real problems and are not reasons to refuse
to evaluate a game whose own inputs are sound.

Not OK as of 2026-08-12 — a snapshot, not a fixed list, and the reason
each is expected:

* `scheduler_running` — CRITICAL, clears the first time the scheduler runs
* `odds_key_configured` — CRITICAL, yours to set
* `odds_freshness` — CRITICAL, no capture since 2026-08-11; clears with the key
* `observation_instant_in_future` — CRITICAL governance, and deliberately
  NOT a gate: 27 records carry an observation instant a month ahead of the
  clock that recorded them, all for `2026_01_SF_LA`. They are invisible to
  every read until wall time passes them and are then admitted as the
  freshest prices available. Re-stamp or quarantine them before 2026-09-10.
* `provider_quota` — WARNING, `UNKNOWN_PLAN`; clears when a real response
  records the plan from the provider's own headers
* `consensus_availability` — WARNING, no consensus until odds are captured
* `injury_freshness` — WARNING, no observation that has actually happened
* `prediction_vintage_coverage` — WARNING, 10 games past a cutoff
* `unmapped_venues` — WARNING, 11 games with no resolved venue
* `weather_freshness` — INFO, no vintage captured yet
* `provenance_unrecorded` / `provenance_non_live_in_live_research` —
  WARNING, 60 legacy records that predate provenance capture

`policy_hash_integrity` and `schedule_freshness` used to appear here and
no longer do: the first reported a blocker that did not exist (it branched
on a parameter no endpoint passes), and the second is genuinely fresh now.

---

## The one thing that is blocked, and stays blocked

```text
POPULATED DATABASE UPGRADE:
BLOCKED — MANUAL_REVIEW_UNRESOLVED
```

Three research evaluations of one prediction against one price, written
about two milliseconds apart, disagreeing about the model probability, with
no record of which run produced which. The unique constraint is not added
to `forward_ledger` on that database and will not be until someone with the
authority decides.

Fresh databases and CI are unaffected — the constraint is declared on the
model, so `create_all` builds it, and the PostgreSQL race tests exercise
it. See `DOMAIN-IDENTITY-GATE.md`.

---

## Secrets

The odds key is read only from `FDE_ODDS_API_KEY`, by the provider adapter
itself. It is never logged, never exposed to frontend code, never
committed, and readiness fails when it is absent. Provider exceptions are
scrubbed before they can reach a log or the job-run table, because the
provider takes the key as a query parameter and httpx puts the request URL
into the message of every error it raises.

bet365 is never retrieved, scraped, inspected, refreshed, automated, or
communicated with. A manually entered price is never described as
currently available.
