# Fourth Down Edge — Populated-Database Conflict Adjudication

**Status: BLOCKED — OWNER DECISION REQUIRED.** This document is a template,
not evidence that any conflict has been resolved. Do **not** commit populated
source rows, financial history, local SQLite files, odds keys, or sensitive
account data to the public repository.

## Preservation record

- Owner/approver:
- Source database (local path, never publish):
- Scheduler/writers confirmed stopped at (UTC):
- Snapshot command / tool version:
- Snapshot SHA-256 (attach private manifest):
- Restored snapshot `PRAGMA integrity_check`:
- Alembic current revision and desired head:
- Pre-upgrade row counts per forward table:
- Identity duplicate inventory report (private):

## Unresolved candidate records — repeat per conflict

| Field | Record A | Record B | Record C (if any) |
|---|---|---|---|
| Table, primary key (private only) | | | |
| Logical identity/version | | | |
| Content hash/version | | | |
| Observed UTC / ingested UTC | | | |
| Prediction vintage and model version | | | |
| Price source and as-of cutoff | | | |
| Recording job/correlation ID | | | |
| Model probability | | | |
| Provenance completeness | | | |
| Downstream references | | | |

### Proposed resolution

- [ ] Retain all conflicting records and block migration until lineage exists.
- [ ] Reconcile to one authoritative identity with explicit provenance (not silent deletion).
- [ ] Preserve all rows in an immutable archival table and mark one canonical **only after** source proof.
- [ ] Other action (precise description):

**Why the other alternatives were rejected:**

**Evidence supporting the chosen interpretation:**

**Anticipated effect on backtests, forward ROI, candidate ranking and CLV:**

**Explicit approval, date/time (UTC) and signature:**

## Disposable-copy migration verification

- Tested snapshot identifier:
- Alembic step-by-step output:
- Unique-constraint verification:
- Pre/post identity row counts and row-level hashes:
- API `/health`, `/v1/forward/slate`, `/v1/forward/live`, `/v1/forward/candidates`, `/v1/forward/health` results:
- Scheduler dry run and rerun/idempotency checks:
- Test suite and workflow run URL:
- Release decision: **DO NOT APPLY TO ORIGINAL** until approved.
