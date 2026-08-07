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

## Where things actually stand

| | now (2026-08-06) | Week 1 (2026-09-10) |
|---|---|---|
| verdict | **BLOCKED** | **PARTIAL** |
| game in slate | no | yes |
| policy window | closed until 2026-09-01 | open (`ftp-2026-v1`) |
| `FDE_ODDS_API_KEY` | not set | not set |

The slate holds **272 games, all `REG`**, from 2026-09-10 to 2027-01-10.

**At Week 1 the only remaining blocker is the provider key.** Everything
else is in place.

### Why tonight cannot be a real test

Three mechanical blockers, and one that no health check would report:

The models are **regular-season models**. `nfl-core-v1` features are built
from prior regular-season games and calibration was fitted on
regular-season outcomes. Preseason is different football — starters play a
series, rosters are ninety deep, and the result is close to noise relative
to team strength. A preseason game pushed through would produce a number
that looks authoritative and means nothing. That is the failure this whole
system exists to refuse.

---

## What you CAN run tonight

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

The account's last recorded balance was ~500 credits, which buys **166
polls a month, about 5.5 a day**. That is roughly a **13× shortfall**
against the configured cadence.

This is a subscription decision, not a bug. What the scheduler does about
it is deliberate: when credits are constrained it sheds cadence on distant
games first and preserves near-kickoff polling, because a stale six-day-out
price costs nothing while a missing close destroys CLV for that game
permanently. Closing captures are protected until the credits are actually
gone.

Thresholds now scale to whatever plan the provider's headers reveal. Until
one real response lands, the plan reads as unknown and the configured
absolutes apply.

---

## Reading the health checks

39 checks, grouped by scope so an operational problem cannot be mistaken
for a decision-integrity one:

| scope | means |
|---|---|
| `DECISION_INPUT` | affects whether a decision can honestly be made |
| `OPERATIONAL_PLATFORM` | the machinery, not the analysis |
| `POSTGAME_EVALUATION` | affects what can be measured afterwards |
| `GOVERNANCE_INTEGRITY` | affects whether the record can be trusted |

Currently not OK, and why each is expected:

* `scheduler_running` — CRITICAL, clears the first time the scheduler runs
* `odds_key_configured` — CRITICAL, yours to set
* `policy_hash_integrity` — CRITICAL, the window opens 2026-09-01
* `provider_quota` — WARNING, clears when a real response records the plan
* `schedule_freshness` — WARNING, last observed 2026-08-01
* `consensus_availability` — WARNING, no consensus until odds are captured
* `weather_freshness` — INFO, no vintage captured yet
* `provenance_unrecorded` / `provenance_non_live_in_live_research` —
  WARNING, 60 legacy records that predate provenance capture

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
