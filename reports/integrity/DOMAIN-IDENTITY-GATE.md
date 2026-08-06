# Closing the domain-identity database gate

Branch `feature/forward-data-capture`, commit `59d85ef`.

Four deficiencies were outstanding. Three of them shared a shape: the
mechanism existed and nothing forced anyone to use it.

---

## 1. The populated-database duplicate block

**Status: formally governed, deliberately not resolved.**

`apps/api/scripts/identity_duplicate_inventory.py` reads the development
database and classifies every logical-identity group. It reuses the
migration's own `_row_values` and `_ensure_aware`, so the inventory and the
migration cannot disagree about what an identity is. It reads only.

The result on this database (`reports/integrity/identity-duplicate-inventory.json`,
sha256 `1f004a0a335b20be`):

| table | rows | UNIQUE | EXACT_DUPLICATE | CONFLICTING_DUPLICATE |
|---|---|---|---|---|
| consensus_snapshots | — | all | 0 | 0 |
| forward_ledger | — | all but one group | 0 | **1** |
| availability_assessments | — | all | 0 | 0 |
| manual_book_price_entries | — | all | 0 | 0 |

The one conflict, characterised in full:

- logical identity `1e8aba7afc2614ca…`, rows `[1, 2, 3]`
- game `2026_01_NE_SEA`, cohort `LIVE_RESEARCH`, `OPENING`,
  `SPREAD|HOME|-3.0|-110`, policy `ftp-2026-v1`, model `market-residual-v1`
- three distinct content hashes: `14904740e937…`, `38634bd43230…`, `9c02ae14728b…`
- differing fields: `model_probability`, `conservative_probability`
- written within about two milliseconds of each other
- `used_downstream: false` — no settlement, CLV, or capture references them

### The governed mechanism

`apps/api/src/fde_api/forward/identity_resolution.py` provides the path the
refusal previously lacked. A manifest, authored by a human, carries one
entry per colliding group:

| disposition | what it asserts | what makes it illegal |
|---|---|---|
| `KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION` | these are different records; the identity was wrong | byte-identical rows, or corrections that do not differ |
| `CANONICALIZE_EXACT_DUPLICATES` | one record written twice | any disagreement in content |
| `SELECT_AUTHORITATIVE_AND_SUPERSEDE` | evidence outside the rows settles it | no evidence recorded |
| `MANUAL_REVIEW_UNRESOLVED` | a human looked and could not settle it | — it is not a resolution |

Every entry must name the rows, restate the content hashes it was authored
against, give a rationale, and name who authorised it. A manifest whose
recorded hashes no longer match the database is refused as stale: it
describes a database that no longer exists, and its author consented to
something else.

**No disposition deletes a row.** Archival and supersession move a row out
of the current `logical_identity_version` namespace, which satisfies the
unique constraint while leaving the row readable. There is no destructive
path, because the moment one exists it becomes the convenient one. A
source-level test asserts the module contains no `DELETE`, `TRUNCATE`, or
`.delete(`.

Supersession does **not** repoint downstream references. A closing capture
records which snapshot was *used*; rewriting it would make the record claim
a snapshot was used that never was. Canonicalisation does repoint, because
there the rows say the same thing.

### The disposition this database gets

`reports/integrity/identity-resolution-manifest.json` records
`MANUAL_REVIEW_UNRESOLVED`, with the reason each resolving disposition is
unavailable:

- **CANONICALIZE** — illegal: three different content hashes. It would
  discard two probabilities.
- **SELECT_AUTHORITATIVE** — requires evidence outside the rows for which
  is right. There is none: these rows predate the decision-context hash, so
  all three backfill to the same legacy context, and nothing records which
  model run produced which number.
- **KEEP_DISTINCT** — requires a real discriminator. "Written at slightly
  different microseconds" is not one, and inventing a run id after the fact
  would be fabricating provenance.

Verified against the real database: the manifest loads, the group
classifies as `CONFLICTING_DUPLICATE`, and `plan.resolved` is `False`.

**Consequence, stated plainly:** the unique constraint is not added to
`forward_ledger` on this development database, and will not be until
somebody with the authority decides. Fresh databases and CI are unaffected —
the constraint is declared on the model, so `create_all` builds it, and the
PostgreSQL race tests exercise it.

`MANUAL_REVIEW_UNRESOLVED` refuses exactly as hard as no manifest at all.
That is tested directly, because the alternative is that "a human looked at
it" quietly becomes a way through.

---

## 2. Research evaluation on the shared identity service

`record_evaluation` was the last of the four services still deciding
idempotency for itself: SELECT, find nothing, INSERT. That pre-check could
not survive contention and could not tell a retry from a contradiction —
finding a row at the slot, it returned that row whatever it said.

It is now `record_evaluation_result`, returning an `IdentityResult` from
`upsert_by_identity`. The logical identity is:

```
canonical_game_id | prediction_identity | price_identity | evaluation_type
| cohort | policy_version | model_version | decision_context_hash
```

The decision-context hash is deliberately inside the identity. A health
remediation therefore produces a genuinely new evaluation rather than
colliding with the suppressed one, and the suppressed one stays exactly as
it was.

Content carries reason **codes**, never rendered sentences — wording is
presentation and must not decide whether two evaluations are the same.

---

## 3. Twelve semantic race families

`apps/api/scripts/verify_race_matrix.py` names all twelve cells (4 entities
× {exact retry, conflicting payload, distinct version}), each bound to a
test at the **public service** boundary. A helper that works says nothing
about whether the service calls it correctly.

The three that were missing are added:

| cell | how the conflict is staged |
|---|---|
| consensus / conflicting_payload | `max_age_minutes`, changing the eligible quote set and therefore the median — content, not identity |
| availability / conflicting_payload | `game_designation` on the underlying observation, changing the derived state |
| evaluation / conflicting_payload | model probability, changing the analytical output |

None asserts *which* caller wins. There is no priority rule between two
simultaneous callers, and asserting one would be asserting a scheduling
accident.

The validator asserts on the **JUnit result**, not the exit code: a run
whose selected tests were all skipped exits 0, so an exit code cannot tell
a passing matrix from an absent one. It requires collected = passed = 12,
skipped = failed = errors = 0, and refuses to count infrastructure tests
(backend PIDs, dialect assertions, no-raw-IntegrityError sweeps) as
semantic cells — those are reported separately.

**Not yet run**: this requires PostgreSQL and runs in CI.

---

## 4. Closing authority, proved behaviourally

`apps/api/tests/test_closing_authority_behaviour.py` sets
`is_closing_capture` on **every** consensus snapshot — the hostile form of
the question — and asserts nothing moves:

- authoritative closing selection
- the referenced closing snapshot
- the `chain-semantic-v2` hash
- CLV and settlement, both stored **and recomputed**
- chain reconciliation verdict
- forward-performance rows

Two readers are classified as legitimate, and each is tested for its remit.
`chain.py` runs a compatibility census: it may count, it may not let the
flag change a current answer. `consensus.py` isolates pre-migration rows
from `latest_consensus_at`: with every row flagged the ordinary lookup
returns nothing, and the close is still exactly what the capture says.

A final test re-runs the whole scheduler chain and confirms no current
service sets the flag — the no-write audit, behaviourally.

10 tests, all passing.

---

## No call site may discard an outcome

Three outcomes exist because CONFLICT means something the caller has to
know. A caller reading `.record` and ignoring `.outcome` gets the right row
and misses that two writers disagreed.

`handled()` and `dispatch()` take all three branches as **required keyword
arguments**, so dropping one is a `TypeError` at the call site rather than
a silence at run time. `consensus.py` now dispatches explicitly rather than
handling outcomes by convention.

`apps/api/scripts/audit_identity_callers.py` catches the other shape of the
same mistake — a service called as a bare expression statement — with a
narrow exemption for calls inside `pytest.raises`, where the value does not
exist to be discarded. Currently passing across 11 files.

Conflicts log hashes and field **names** only. Never values: for a price
observation those are user-entered.

---

## Verification

| gate | result |
|---|---|
| Python suite (non-PostgreSQL) | **973 passed**, 0 failed, 0 skipped |
| Ruff (`src`, `tests`, `scripts`, `migrations`) | clean |
| mypy (76 source files) | clean |
| Identity caller audit | passed, 0 discarded outcomes |
| Duplicate inventory | 1 conflicting group, governed |
| Direct/scheduler parity | zero-difference, `747707117a20…` both paths |
| TypeScript typecheck | clean |
| TypeScript tests | 57 passed |
| RLS / migration validation | all checks passed |
| Production build | succeeded |
| Playwright e2e | 1 passed |
| `npm audit --production` | 2 moderate, exit 0 |
| Secret audit | `FDE_ODDS_API_KEY` read only from the environment; no literal anywhere |
| PostgreSQL race matrix (12 cells) | **CI only** |
| PostgreSQL identity / chain / concurrency gates | **CI only** |

Parity was regenerated after evaluation enforcement was active and remains
zero-difference.

### New CI gates, each independent

```
Migration duplicate-resolution gate    tests/test_identity_resolution.py
Identity caller audit                  scripts/audit_identity_callers.py
PostgreSQL race matrix (12 cells)      scripts/verify_race_matrix.py
Closing-authority behavioural tests    tests/test_closing_authority_behaviour.py
Sequential identity and inventory      tests/test_domain_identity.py + migration
```

They are separate steps with `if: always()` so one cannot hide another.

---

## Standing constraints

Unchanged and observed: paper mode only, no BET state reachable; no
bet365 contact of any kind; fixture output never described or stored as
live-provider output; the odds key read only from `FDE_ODDS_API_KEY` and
never logged, exposed, or committed; Phase 2 branches and tags untouched;
nothing merged or deployed; no force push.

No claim is made about profitability, predictive superiority, production
approval, or real-money readiness.
