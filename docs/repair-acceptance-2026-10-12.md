# Fourth Down Edge — Release 0 repair gates

Scope: **feature/forward-data-capture**, not the older demo `master` branch.
This set of changes is deliberately non-destructive. It does not modify, copy, or
repair the existing `apps/api/data/fde.db` file and does not supply odds keys.

## Gate A — Local captured-data database

The last branch commit on August 16 reports that the populated SQLite database
cannot serve `consensus_snapshots`, `forward_ledger`, or
`availability_assessments` because the application requires `cohort` columns.
The `b7e2f9c41a68` migration refuses unresolved conflicting logical identities.
The runbook explicitly identifies three conflicting evaluations; automatically
picking one would destroy potentially important audit evidence.

Before an owner-authorized intervention:

1. **Stop all scheduler and engine processes.** Obtain a verified binary copy
   of the original SQLite database, its WAL and SHM sidecars if present, plus
   an SHA-256 manifest for the snapshot. Keep the originals untouched.
2. Run `python apps/api/scripts/diagnose_local_database.py
   apps/api/data/fde.db --json` against the stopped database. The inspector
   opens the database with SQLite `mode=ro` and `query_only=ON`.
3. Reconcile the exact conflicting records using their source evidence and
   policy/model run lineage; preserve every original row in the audit record.
   Do not mark an unresolved conflict as resolved in a manifest.
4. Test an Alembic upgrade **only on a copy** of the database, then compare
   row counts, identities, provenance and scheduler replay invariants.
5. Do not repoint the engine at the upgraded copy until validation passes
   and the owner approves the substitution.

The diagnostic cannot prove full migration safety, classify conflicting
records, or certify odds and models. Its status is a triage signal.

## Gate B — Real engine smoke test

The existing `live-screens.spec.ts` deliberately allows either live data
OR engine down. Keep that as a fail-safe UX test, but it is NOT evidence
that live integration works. The engine-only spec is deliberately kept under `tests/engine-e2e/`, OUTSIDE
`tests/e2e/`, because the established JS CI runs `npx playwright test`
without starting Python. Placing mandatory live-engine assertions in that
folder would break the existing independent frontend gate. The new suite
requires a real FastAPI process
and validates API responses and visible live pages. It never approves a
model or interprets empty datasets as opportunities.

Run with an isolated **disposable** database, not captured production data:

```bash
# from repo root, in a bash shell (CI does this automatically)
npm ci
cd apps/api
uv sync --locked
mkdir -p data
FDE_DATABASE_URL=sqlite:///./data/ci_live_acceptance.db uv run alembic upgrade head
FDE_DATABASE_URL=sqlite:///./data/ci_live_acceptance.db FDE_ALLOW_UNAUTHENTICATED=1 uv run uvicorn fde_api.api.main:app --host 127.0.0.1 --port 8000
# in a second shell, from repo root:
npx playwright test --config=playwright.live.config.ts
```

The test suite must fail when the engine is stopped and pass only when API
contracts, migration and browser integration all work. An empty fresh test
database is acceptable at this gate; it is not proof of working live odds,
current NFL games, scheduler capture, or validated predictions.

## Gate C — Next work after A/B

* Create a seeded, isolated integration fixture containing schedule, quote,
  eligible consensus, prediction vintage and append-only research evaluation.
* Require a browser flow covering one specific fixture through Live Slate,
  Game Lab, manually entered paper price, immutable paper ledger and outcome.
* Eliminate misleading `REAL_TRACKING` toggle behavior or implement a
  distinct tracking ledger with explicit origin and safeguards.
* Distinguish the old GitHub Pages demo deployed from `master` from the
  under-development Python engine. A static site cannot host FastAPI.
* Do not enable real-money model recommendations. Validate walk-forward and
  forward results, model calibration and verified price availability first.

## Verification constraints

This patch was prepared from GitHub source code without repository cloning;
local Node dependency installation and Python FastAPI integration tests were
not executable in the preparation environment. CI results are required after
it is applied. Passing the diagnostic's standalone unit tests is not the
same as passing the live engine acceptance suite. Frontend syntax parsing
is not equivalent to a TypeScript typecheck or an end-to-end browser pass.

## Release 0A — local backup utility (new)

`apps/api/scripts/snapshot_capture_database.py` opens the source with SQLite
`mode=ro`, copies it through SQLite's `backup()` method to a **new** private
file, verifies `PRAGMA integrity_check`, and creates a SHA-256 manifest. It
never deletes source rows or writes migrations. Example, after stopping the
scheduler and writers:

```bash
python apps/api/scripts/snapshot_capture_database.py \
  apps/api/data/fde.db /secure-private-backups/fde-before-migration.db
```

Keep the snapshot and `.manifest.json` PRIVATE; never attach them to a public
PR or upload them to CI. This utility does not adjudicate the three
conflicting evaluations; use `docs/migration-decision-log-template.md`.
