# Recovery lineage — concurrency and locking strategy

What guarantees the scheduler makes when several workers act on the same
logical slot at the same time, how each guarantee is enforced, and what is
verified against which database.

This document exists because the guarantees were previously enforced only
against SQLite. SQLite serialises writers at the file level, so a race that
"passes" there proves nothing about PostgreSQL — and two defects that had
passed hundreds of local runs failed the first time they met a real server
(see [What the second environment found](#what-the-second-environment-found)).

## 1. The invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | One run per (job, cohort, logical slot, params) | `UNIQUE` on `scheduled_job_runs.idempotency_key` |
| 2 | At most one active member per recovery chain | `active_member` check inside `build_recovery_lineage` |
| 3 | A chain is a line, never a tree | `recovery_sequence` increments by exactly one; one row per predecessor |
| 4 | Exactly one worker owns a startup reconciliation | Conditional `UPDATE … WHERE job_outcome = 'RUNNING'` |
| 5 | A recovery may not change slot, cohort, policy or provider mode | `validate_recovery` |
| 6 | A terminal-failure chain reopens only by administrative override | `validate_recovery` |

## 2. Locking strategy

**No advisory locks. No `SELECT … FOR UPDATE`. No dependence on isolation
level.** Every mutual-exclusion point is a single atomic statement whose
outcome the loser can observe.

### 2.1 Claiming a slot — the insert *is* the lock

`_claim` inserts the run row. `idempotency_key` is `UNIQUE`, so a second
worker's insert fails with `IntegrityError` and it reports `skipped`. There
is no window between "decided to run" and "recorded that I am running",
because they are the same statement.

This works identically on SQLite and PostgreSQL: both enforce the unique
index at insert time.

### 2.2 Reconciling a crashed run — a guarded transition

`reconcile_startup` transitions `RUNNING → INTERRUPTED`. It selects
candidates and then issues, per candidate:

```sql
UPDATE scheduled_job_runs
   SET job_outcome = 'INTERRUPTED', status = 'interrupted', …
 WHERE id = :id
   AND job_outcome = 'RUNNING'
```

The guard is the state being transitioned *from*. Exactly one worker's
statement matches a row; the others match zero and report nothing
reconciled. `rowcount` is the ownership signal.

The projection to the compatibility `status` column still goes through
`apply_state`, but it is applied to a **transient** stand-in rather than the
persistent row. Mutating the persistent row would autoflush the new state
before the guarded `UPDATE` ran, so the guard would match zero rows and
*nobody* would claim the transition. There is a regression test for that
failure mode specifically.

### 2.3 Opening a recovery — read-then-validate under the same insert

`build_recovery_lineage` refuses when the chain already has an active
member. This read is not itself locked; it does not need to be, because the
subsequent `_claim` carries the recovery key through the same `UNIQUE`
index. Two workers that both pass the check still produce one row: the
recovery key is derived from (original key, root run id, sequence), so both
compute the *same* key and the second insert loses.

That is the reason recovery keys are derived rather than random.

## 3. Isolation level

The gate runs at PostgreSQL's default, **read committed** — recorded in the
CI log by `report_backend`, not assumed. Nothing above requires a stricter
level:

* §2.1 and §2.3 rely on a unique index, which is enforced regardless of
  isolation level.
* §2.2 relies on a single `UPDATE` matching a predicate, which is atomic
  within its own statement at read committed.

If a future change introduces a read-then-write that is *not* reducible to
one guarded statement, this section stops being true and the change needs
either a stricter level or an explicit lock. Note that here rather than
discovering it in production.

## 4. What is verified, and against what

| Property | SQLite suite | PostgreSQL gate |
|---|---|---|
| Functional behaviour of recovery lineage | yes | yes |
| Migrations apply, upgrade, downgrade, re-upgrade | yes | yes |
| Genuine multi-connection contention | **no** | yes |
| `VARCHAR(n)` width enforcement | simulated (see §5) | native |

The PostgreSQL gate is a separate CI step selected by the `pg_concurrency`
marker. Its fixtures **raise** when `FDE_DATABASE_URL` is absent rather than
falling back to SQLite: a race that SQLite serialised away, reported as
verified, would be worse than not running the gate at all.

Each test builds its own PostgreSQL schema, each worker builds its own
`Scheduler` and therefore takes its own pooled connection, and the suite
asserts `pg_backend_pid()` differs between sessions before racing on
anything. A `threading.Barrier` releases the workers together so contention
is repeatable rather than a matter of scheduling luck.

The gate prints its backend on every run — server version, database,
isolation level, lock and statement timeouts, max connections, driver
versions — so a passing result states on the record what it actually
exercised.

## 5. What the second environment found

Both of these passed every local run and failed against a real server. They
are recorded because they characterise the *class* of defect this gate
exists to catch, not because either is still open.

**An unbounded idempotency key.** `make_idempotency_key` inlined the params
dict into the key, so the key was as long as the payload.
`idempotency_key` is `VARCHAR(160)`. SQLite ignores VARCHAR length and
stores whatever it is given; PostgreSQL rejected the insert with
`StringDataRightTruncation`. The key is now bounded by construction — params
contribute a digest, and the base key is capped so the worst-case recovery
suffix still fits.

The suite no longer depends on the gate to catch the next one: a mapper
listener rejects any ORM write exceeding a declared VARCHAR width, so
SQLite is no longer more permissive than PostgreSQL. It sees ORM inserts
and updates, not Core `insert()` or raw SQL — those still depend on the
gate.

**A non-atomic reconciliation.** Described in §2.2. Several workers all saw
the same `RUNNING` row and all reported reconciling it. The row ended up
correct — they wrote identical values — but each worker believed it owned
the transition, and ownership is what decides who may open the recovery.
It surfaced on Linux CI as a count of 2 where the invariant is 1.

## 6. Status

**PostgreSQL functional integration: VERIFIED.** Migrations apply, upgrade
from the prior revision, and downgrade/re-upgrade against PostgreSQL 16.14
in CI.

**PostgreSQL recovery-lineage concurrency: VERIFIED.** The dedicated gate
passed on PostgreSQL 16.14 at read committed, 11 tests, with independent
backend PIDs asserted. Scope is the recovery-lineage races enumerated in
§1 — initial-run, recovery, administrative-recovery, and mixed
initial-versus-recovery contention. It is not a general statement about
concurrency elsewhere in the system.

Neither statement is a claim about model quality, profitability, or
readiness for real-money use. Nothing in this document bears on those.
