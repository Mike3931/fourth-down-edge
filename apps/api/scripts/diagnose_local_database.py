"""Read-only diagnostic for a Fourth Down Edge SQLite capture database.

This intentionally does NOT invoke Alembic, change the schema, resolve
ambiguous duplicates, or open the database in writable mode. It prints
only schema information and duplicate counts; no betting records or keys.

Usage:
    python apps/api/scripts/diagnose_local_database.py apps/api/data/fde.db
    python apps/api/scripts/diagnose_local_database.py apps/api/data/fde.db --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# These columns are required by the post-d8f41c6a3b92 application models.
REQUIRED_COLUMNS: dict[str, set[str]] = {
    "consensus_snapshots": {"cohort", "min_books_applied"},
    "forward_ledger": {"cohort"},
    "availability_assessments": {"cohort"},
}

# Content-addressed identity is present on these forward-capture tables.
IDENTITY_TABLES = (
    "consensus_snapshots",
    "forward_ledger",
    "availability_assessments",
    "manual_book_price_entries",
)


def inspect(path: Path) -> dict[str, object]:
    """Inspect the on-disk file without creating or modifying it."""
    db = path.expanduser().resolve()
    if not db.is_file():
        raise FileNotFoundError(f"Database file does not exist: {db}")

    # SQLite URI mode=ro is crucial: the default sqlite3.connect(path) creates
    # a new DB when the path is mistyped, which masks a missing capture DB.
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")  # one coherent view across all diagnostic reads
        tables = {
            name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing: dict[str, list[str]] = {}
        table_rows: dict[str, int] = {}
        duplicate_groups: dict[str, int] = {}

        for table, required in REQUIRED_COLUMNS.items():
            if table not in tables:
                missing[table] = sorted(required)
                continue
            columns = {
                row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            absent = sorted(required - columns)
            if absent:
                missing[table] = absent

        for table in IDENTITY_TABLES:
            if table not in tables:
                continue
            table_rows[table] = int(
                conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            columns = {
                row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            if {"logical_identity_version", "logical_identity_hash"} <= columns:
                # Report duplicate identity GROUP counts, never display the
                # private records or imply a specific group is safe to drop.
                duplicate_groups[table] = int(conn.execute(
                    f'SELECT COUNT(*) FROM (SELECT 1 FROM "{table}" '
                    'WHERE logical_identity_hash IS NOT NULL '
                    'GROUP BY logical_identity_version, logical_identity_hash '
                    'HAVING COUNT(*) > 1)'
                ).fetchone()[0])

        versions = (
            [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]
            if "alembic_version" in tables else []
        )
        status = (
            "MANUAL_REVIEW_REQUIRED" if any(duplicate_groups.values())
            else "SCHEMA_INCOMPLETE" if missing
            else "SCHEMA_COLUMNS_PRESENT"
        )
        return {
            "database": str(db),
            "status": status,
            "alembic_versions": versions,
            "missing_required_columns": missing,
            "identity_duplicate_group_counts": duplicate_groups,
            "forward_table_row_counts": table_rows,
            "disclaimer": (
                "Schema check only. No migration was attempted. Passing this "
                "check does not establish migration safety, data correctness, "
                "provider readiness, or model validity. Back up the database "
                "and review migration conflicts before any write."
            ),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing local SQLite database")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)
    try:
        report = inspect(args.database)
    except (OSError, sqlite3.DatabaseError) as exc:
        print(f"INSPECTION_FAILED: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Status: {report['status']}")
        print(f"Alembic version(s): {report['alembic_versions']}")
        print(f"Missing columns: {report['missing_required_columns']}")
        print(f"Duplicate identity groups: {report['identity_duplicate_group_counts']}")
        print(str(report["disclaimer"]))
    return 0 if report["status"] == "SCHEMA_COLUMNS_PRESENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
