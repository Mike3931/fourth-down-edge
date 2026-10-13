# Release 0 implementation evidence

Base: origin/feature/forward-data-capture f388e6954ab886ab11990272d8d10cc344b9e52c. Existing C:/Users/mjcun/NFL checkout and installed application retained. Isolated development checkout only. Handoff manifest verified (22 files).

Implemented readonly SQLite diagnostics, exclusive-create SQLite backup with closed connections and self-contained WAL snapshot, migration decision template, separate mandatory engine browser gate and feature-branch CI coverage. No production migration, deployment or model changes.

Local checks: production build passed; npm test 239 tests passed (177 calculations, 62 client); npm run test:db passed PGlite schema/RLS/append-only guards (not a live PostgreSQL server); Ruff src/tests/scripts/migrations clean; mypy 76 modules clean; 67 targeted utility/migration/resolution tests passed before two added WAL/manifest tests; new utility tests 11 passed. Five mandatory real FastAPI/browser checks passed on isolated migrated SQLite at port 18060, frontend 5176. Negative gate deliberately pointed at absent service port 18199: one selected backend test failed, exit 1. Bundle credential canary audit passed. Production npm audit has zero high/critical and two moderate router findings; full development audit remains informational.

Full Python suite and CI PostgreSQL matrix pending at initial PR creation. Do not merge until required checks pass.

Private captured-data snapshot remains outside repository. Its schema is newer than the audited branch (f6d92c4ab811); no downgrade attempted. Stored-identity diagnostic found no duplicate hashes; semantic inventory found ONE conflicting forward-ledger group. This distinction is material: column presence is not conflict clearance. Every source row retained. Private inventory and snapshot manifest are local only; no record contents uploaded. No canonical choice or relabeling authorized or performed.

Provider/live model readiness is not established by an empty migrated-database gate. Source capture, real current NFL journey and full browser/mobile acceptance follow in Release 1A.

First CI source gates passed, including PostgreSQL engine, frontend, dependencies and mandatory engine browser checks. Live acceptance workflow failed only during setup-uv post-job cache pruning after five minutes (uv 0.13.0 exit 2). Pin tested uv 0.12.1 and disable optional cache pruning; rerun exact commit CI. This is an infrastructure repair, not a waived source check.
