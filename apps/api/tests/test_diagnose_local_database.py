"""Fixture-based, standard-library verification of the read-only DB audit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_local_database.py"


def _invoke(db: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(db), "--json"],
        capture_output=True, text=True, check=False,
    )
    return result, json.loads(result.stdout) if result.stdout else None


def _write_db(db: Path, columns: bool, duplicates: bool = False):
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('b7e2f9c41a68')")
        consensus = (
            "CREATE TABLE consensus_snapshots (cohort TEXT, min_books_applied INT, "
            "logical_identity_version TEXT, logical_identity_hash TEXT)"
            if columns else
            "CREATE TABLE consensus_snapshots (logical_identity_version TEXT, logical_identity_hash TEXT)"
        )
        conn.execute(consensus)
        conn.execute("CREATE TABLE forward_ledger (cohort TEXT)" if columns
                     else "CREATE TABLE forward_ledger (id INT)")
        conn.execute("CREATE TABLE availability_assessments (cohort TEXT)" if columns
                     else "CREATE TABLE availability_assessments (id INT)")
        if duplicates:
            conn.execute("INSERT INTO consensus_snapshots "
                         "(logical_identity_version, logical_identity_hash) VALUES ('v1', 'same')")
            conn.execute("INSERT INTO consensus_snapshots "
                         "(logical_identity_version, logical_identity_hash) VALUES ('v1', 'same')")


def test_missing_file_does_not_get_created(tmp_path):
    missing = tmp_path / "wrong.db"
    proc, payload = _invoke(missing)
    assert proc.returncode == 2
    assert payload is None
    assert not missing.exists()


def test_outdated_schema_is_reported_without_changes(tmp_path):
    db = tmp_path / "old.db"
    _write_db(db, columns=False)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    proc, payload = _invoke(db)
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert proc.returncode == 1
    assert payload["status"] == "SCHEMA_INCOMPLETE"
    assert payload["missing_required_columns"]["consensus_snapshots"] == [
        "cohort", "min_books_applied"
    ]
    assert before == after


def test_duplicate_identities_require_review(tmp_path):
    db = tmp_path / "conflict.db"
    _write_db(db, columns=True, duplicates=True)
    proc, payload = _invoke(db)
    assert proc.returncode == 1
    assert payload["status"] == "MANUAL_REVIEW_REQUIRED"
    assert payload["identity_duplicate_group_counts"]["consensus_snapshots"] == 1
    assert "same" not in json.dumps(payload)  # no record hash is printed


def test_current_schema_only_means_columns_present(tmp_path):
    db = tmp_path / "columns.db"
    _write_db(db, columns=True)
    proc, payload = _invoke(db)
    assert proc.returncode == 0
    assert payload["status"] == "SCHEMA_COLUMNS_PRESENT"
    assert "does not establish migration safety" in payload["disclaimer"]
