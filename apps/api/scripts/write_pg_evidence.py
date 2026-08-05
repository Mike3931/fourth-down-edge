"""Write the PostgreSQL recovery-concurrency evidence artifact.

A link to a CI run is not evidence that survives. GitHub expires logs,
runs can be deleted, and a URL in a report proves only that someone once
had a URL. This writes the facts themselves into the repository, hashed,
so the claim "PostgreSQL recovery-lineage concurrency was verified" can be
checked years later against something that does not depend on a third
party still serving a page.

The hash covers the canonical JSON of every field except the hash itself,
LF-normalised. `tests/test_pg_evidence_artifact.py` recomputes it, so an
edit to the artifact that is not also an edit to the recorded facts fails
the suite.

Run:  python scripts/write_pg_evidence.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ARTIFACT = (
    Path(__file__).resolve().parents[3] / "reports" / "integrity"
    / "pg-recovery-concurrency-evidence.json"
)

EVIDENCE: dict[str, Any] = {
    "artifact": "postgresql-recovery-lineage-concurrency-evidence",
    "artifact_version": "1",
    "claim": (
        "The recovery-lineage concurrency invariants were verified against a real "
        "PostgreSQL server with genuinely independent connections. This is not a "
        "claim about concurrency elsewhere in the system, and not a claim about "
        "model quality, profitability, or readiness for real-money use."
    ),
    "verified_at_utc": "2026-08-04T13:05:28Z",
    "ci": {
        "provider": "github-actions",
        "workflow": "deploy.yml",
        "run_id": "30914640864",
        "run_url": (
            "https://github.com/Mike3931/fourth-down-edge/actions/runs/30914640864"
        ),
        "url_is_supplementary": True,
        "trigger": "workflow_dispatch",
        "branch": "feature/forward-data-capture",
        "head_sha": "2e95f6e6f23b460cd2718fe291fe932a59cd54db",
        "job": "Analytical engine (Python)",
        "job_result": "success",
        "step": "PostgreSQL recovery-lineage concurrency gate",
        "step_result": "success",
    },
    "backend": {
        "dialect": "postgresql",
        "server_version": "16.14 (Debian 16.14-1.pgdg13+1)",
        "database": "fde_test",
        "isolation_level": "read committed",
        "default_transaction_isolation": "read committed",
        "lock_timeout": "0",
        "statement_timeout": "0",
        "max_connections": "100",
        "sqlalchemy": "2.0.51",
        "psycopg": "3.2.13",
    },
    "execution": {
        "racing_workers": 4,
        "connection_pool_size": 8,
        "connection_pool_max_overflow": 4,
        "independent_connections_asserted": True,
        "independence_method": (
            "pg_backend_pid() compared between two concurrently open sessions; "
            "the suite fails if they match"
        ),
        "race_synchronisation": (
            "threading.Barrier(n) released simultaneously, so contention is "
            "repeatable rather than dependent on scheduling luck"
        ),
        "isolation_between_tests": (
            "one PostgreSQL schema per test via schema_translate_map, created and "
            "dropped around each case"
        ),
        "fallback_to_sqlite": (
            "impossible: pgconftest.require_pg_url raises when FDE_DATABASE_URL is "
            "absent or not a PostgreSQL URL, rather than defaulting"
        ),
    },
    "tests": {
        "total": 11,
        "passed": 11,
        "failed": 0,
        "deselected_non_gate_tests": 614,
        "duration_seconds": 4.39,
        "selection": 'pytest -m "pg_concurrency"',
        "groups": [
            {
                "name": "TestBackendIsReallyPostgres",
                "count": 3,
                "result": "passed",
                "proves": (
                    "the dialect is postgresql, the module constructs no sqlite URL, "
                    "and two sessions hold two different backend PIDs"
                ),
            },
            {
                "name": "TestInitialRunRace",
                "count": 3,
                "result": "passed",
                "proves": (
                    "four workers claiming the same slot produce exactly one finished "
                    "run and one set of domain effects; the losers receive a typed skip"
                ),
            },
            {
                "name": "TestRecoveryRace",
                "count": 3,
                "result": "passed",
                "proves": (
                    "four workers recovering the same interrupted slot produce exactly "
                    "one recovery branch at sequence 1, with a monotonic gap-free "
                    "sequence, one root, and no duplicated domain effects"
                ),
            },
            {
                "name": "TestAdministrativeRecoveryRace",
                "count": 1,
                "result": "passed",
                "proves": (
                    "concurrent administrative overrides do not stack: at most one "
                    "takes effect, and it carries operator and reason"
                ),
            },
            {
                "name": "TestInitialVersusRecoveryRace",
                "count": 1,
                "result": "passed",
                "proves": (
                    "ordinary recovery racing an administrative override yields exactly "
                    "one active successor under one root"
                ),
            },
        ],
    },
    "results": {
        "initial_run_race": "PASSED",
        "recovery_race": "PASSED",
        "administrative_recovery_race": "PASSED",
        "mixed_initial_versus_recovery_race": "PASSED",
    },
    "locking_strategy": {
        "summary": (
            "every mutual-exclusion point is a single atomic statement whose outcome "
            "the loser can observe"
        ),
        "slot_claim": "UNIQUE index on scheduled_job_runs.idempotency_key; the insert is the lock",
        "startup_reconciliation": (
            "UPDATE ... WHERE id = :id AND job_outcome = 'RUNNING'; rowcount is the "
            "ownership signal"
        ),
        "recovery_open": (
            "recovery keys are derived, not random, so two workers compute the same key "
            "and the second insert loses on the same unique index"
        ),
        "advisory_locks": "none",
        "select_for_update": "none",
        "requires_stricter_isolation": False,
        "documentation": "docs/recovery-concurrency.md",
    },
    "defects_this_gate_found": [
        {
            "defect": "unbounded idempotency key overflowed VARCHAR(160)",
            "why_sqlite_missed_it": "SQLite does not enforce VARCHAR length",
            "postgres_error": "psycopg.errors.StringDataRightTruncation",
            "fixed_in": "1391661",
        },
        {
            "defect": "reconcile_startup was a non-atomic SELECT-then-write",
            "why_sqlite_missed_it": (
                "the threads happened to serialise on Windows; the race is real on "
                "both backends and surfaced on Linux CI as a count of 2 where the "
                "invariant is 1"
            ),
            "postgres_error": "none - a wrong count, not an exception",
            "fixed_in": "6c8732f",
        },
    ],
    "scope_limits": [
        "Covers recovery-lineage races only, as enumerated under tests.groups.",
        "Not a general statement about concurrency elsewhere in the system.",
        "Not a statement about model quality, profitability, predictive superiority, "
        "production approval, or readiness for real-money use.",
    ],
}


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """LF-normalised canonical JSON, excluding the hash field.

    Sorted keys and an explicit separator so the same facts hash the same
    on every platform. Raw-byte hashing of a written file is what made an
    earlier governance artifact hash differently on Windows and Linux; this
    hashes the DATA, not the file.
    """
    body = {k: v for k, v in payload.items() if k != "evidence_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def build() -> dict[str, Any]:
    payload = dict(EVIDENCE)
    payload["evidence_sha256"] = evidence_hash(payload)
    return payload


def main() -> None:
    payload = build()
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {ARTIFACT}")
    print(f"evidence_sha256 {payload['evidence_sha256']}")


if __name__ == "__main__":
    main()
