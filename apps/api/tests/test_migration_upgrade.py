"""Upgrade-migration safety for the outcome/domain-state split.

A fresh-database migration proves nothing about existing data. These
tests build a database at the PRIOR revision (the schema as of commit
ef11775), insert a row for every legacy state value, upgrade to HEAD, and
assert the deterministic mapping — including that conservative defaults
are marked as such rather than presented as reconstructed fact.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

API_ROOT = Path(__file__).resolve().parents[1]

# The revision that shipped in ef11775, before the split.
PRIOR_REVISION = "34134455937d"
SPLIT_REVISION = "c034f7ebc265"

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

# (legacy status, recorded error_summary, expected outcome, expected domain, exact?)
LEGACY_ROWS: list[tuple[str, str | None, str, str, bool]] = [
    ("finished", None, "SUCCESS", "COMPLETE", True),
    ("running", None, "RUNNING", "NOT_YET_AVAILABLE", True),
    ("failed", "provider timeout", "RETRYABLE_FAILURE", "DATA_INCOMPLETE", True),
    ("dead_letter", "gave up", "TERMINAL_FAILURE", "DATA_INCOMPLETE", True),
    ("interrupted", "process died", "INTERRUPTED", "DATA_INCOMPLETE", True),
    # No metadata: the domain state cannot be reconstructed, so the mapping
    # is conservative and flagged inexact.
    ("skipped", None, "SKIPPED", "NO_ELIGIBLE_RECORDS", False),
    # Metadata present: the recorded domain state wins over the default.
    ("skipped", "outcome=SKIPPED; domain_state=DATA_INCOMPLETE",
     "SKIPPED", "DATA_INCOMPLETE", True),
    ("finished", "outcome=SUCCESS_WITH_WARNINGS; domain_state=SUPPRESSED",
     "SUCCESS", "SUPPRESSED", True),
    ("finished", "outcome=SUCCESS_WITH_WARNINGS; domain_state=NOT_APPLICABLE",
     "SUCCESS", "NOT_APPLICABLE", True),
]


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, "FDE_DATABASE_URL": db_url}
    return subprocess.run(
        [str(API_ROOT / ".venv" / "Scripts" / "python"), "-m", "alembic", *args],
        cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
    )


@pytest.fixture()
def legacy_db(tmp_path) -> str:
    """A database at the pre-split revision, populated with legacy rows."""
    db = tmp_path / "legacy.db"
    url = f"sqlite:///{db.as_posix()}"
    r = _alembic(url, "upgrade", PRIOR_REVISION)
    assert r.returncode == 0, r.stderr

    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for i, (status, summary, *_rest) in enumerate(LEGACY_ROWS):
            conn.execute(
                text(
                    "INSERT INTO scheduled_job_runs "
                    "(id, job_kind, idempotency_key, data_mode, scheduled_for, started_at, "
                    " completed_at, status, retry_count, provider_calls, records_received, "
                    " records_written, error_summary, code_commit, created_at) "
                    "VALUES (:id, :kind, :ik, :dm, :slot, :start, :done, :st, 0, 1, 5, 3, "
                    " :summary, :commit, :created)"
                ),
                {
                    "id": f"run_legacy_{i}", "kind": "odds_capture",
                    "ik": f"odds_capture:burn_in:slot{i}", "dm": "LIVE_RESEARCH",
                    "slot": T0, "start": T0, "done": T0, "st": status,
                    "summary": summary, "commit": "ef11775", "created": T0,
                },
            )
    engine.dispose()
    return url


class TestUpgradeFromPriorRevision:
    def test_upgrade_succeeds(self, legacy_db: str) -> None:
        r = _alembic(legacy_db, "upgrade", "head")
        assert r.returncode == 0, r.stderr

    def test_every_legacy_value_maps_correctly(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            for i, (status, _summary, exp_outcome, exp_domain, _exact) in enumerate(LEGACY_ROWS):
                row = conn.execute(
                    text("SELECT status, job_outcome, domain_state FROM scheduled_job_runs "
                         "WHERE id = :id"),
                    {"id": f"run_legacy_{i}"},
                ).one()
                assert row[0] == status, f"row {i}: legacy value must be preserved"
                assert row[1] == exp_outcome, f"row {i} outcome"
                assert row[2] == exp_domain, f"row {i} domain"
        engine.dispose()

    def test_identifiers_and_timestamps_unchanged(self, legacy_db: str) -> None:
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            before = conn.execute(
                text("SELECT id, idempotency_key, created_at, scheduled_for "
                     "FROM scheduled_job_runs ORDER BY id")
            ).fetchall()
        engine.dispose()
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            after = conn.execute(
                text("SELECT id, idempotency_key, created_at, scheduled_for "
                     "FROM scheduled_job_runs ORDER BY id")
            ).fetchall()
        engine.dispose()
        assert before == after

    def test_run_history_remains_queryable(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM scheduled_job_runs")).scalar()
            by_outcome = conn.execute(
                text("SELECT job_outcome, COUNT(*) FROM scheduled_job_runs GROUP BY job_outcome")
            ).fetchall()
        engine.dispose()
        assert n == len(LEGACY_ROWS)
        assert dict(by_outcome)["SUCCESS"] >= 1

    def test_migrated_rows_carry_recovery_lineage_defaults(self, legacy_db: str) -> None:
        """Migrated rows become their own root at sequence zero."""
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, root_run_id, recovery_sequence, original_idempotency_key, "
                     "administrative_override FROM scheduled_job_runs")
            ).fetchall()
        engine.dispose()
        for run_id, root, seq, orig_key, override in rows:
            assert root == run_id  # own root
            assert seq == 0
            assert orig_key
            assert not override

    def test_no_leftover_null_states(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            nulls = conn.execute(
                text("SELECT COUNT(*) FROM scheduled_job_runs "
                     "WHERE job_outcome IS NULL OR domain_state IS NULL")
            ).scalar()
        engine.dispose()
        assert nulls == 0


class TestMigrationAudit:
    def test_audit_row_per_reinterpreted_record(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT record_id, original_value, new_outcome, new_domain_state, "
                     "mapping_rule, exact, revision, table_name FROM migration_audit")
            ).fetchall()
        engine.dispose()
        assert len(rows) == len(LEGACY_ROWS)
        assert all(r[6] == SPLIT_REVISION for r in rows)
        assert all(r[7] == "scheduled_job_runs" for r in rows)
        assert all(r[4] for r in rows)  # every row names its rule

    def test_conservative_mappings_are_flagged_inexact(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            audit = {
                r[0]: (r[1], bool(r[2]))
                for r in conn.execute(
                    text("SELECT record_id, mapping_rule, exact FROM migration_audit")
                ).fetchall()
            }
        engine.dispose()
        for i, (_status, _summary, _o, _d, exact) in enumerate(LEGACY_ROWS):
            rule, recorded_exact = audit[f"run_legacy_{i}"]
            assert recorded_exact == exact, f"row {i}: {rule}"
            if not exact:
                assert "conservative" in rule.lower()

    def test_metadata_refinement_is_recorded(self, legacy_db: str) -> None:
        """A domain state recovered from metadata must say so."""
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            rule = conn.execute(
                text("SELECT mapping_rule FROM migration_audit WHERE record_id = :r"),
                {"r": "run_legacy_6"},  # skipped + recorded DATA_INCOMPLETE
            ).scalar()
        engine.dispose()
        assert "refined from recorded metadata" in rule

    def test_audit_contains_no_payload_content(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(migration_audit)")).fetchall()]
        engine.dispose()
        for forbidden in ("payload", "params", "result", "error_summary"):
            assert forbidden not in cols


class TestDowngrade:
    def test_downgrade_is_supported_and_preserves_legacy_rows(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        r = _alembic(legacy_db, "downgrade", PRIOR_REVISION)
        assert r.returncode == 0, r.stderr
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, status FROM scheduled_job_runs ORDER BY id")
            ).fetchall()
        engine.dispose()
        # The legacy column was never dropped, so downgrade is lossless for it.
        assert len(rows) == len(LEGACY_ROWS)
        assert {r[1] for r in rows} >= {"finished", "skipped", "failed"}

    def test_upgrade_downgrade_upgrade_is_stable(self, legacy_db: str) -> None:
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        assert _alembic(legacy_db, "downgrade", PRIOR_REVISION).returncode == 0
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            nulls = conn.execute(
                text("SELECT COUNT(*) FROM scheduled_job_runs WHERE job_outcome IS NULL")
            ).scalar()
            audits = conn.execute(text("SELECT COUNT(*) FROM migration_audit")).scalar()
        engine.dispose()
        assert nulls == 0
        assert audits == len(LEGACY_ROWS)  # not duplicated by the re-run


class TestMappingTableCompleteness:
    def test_every_job_status_value_has_a_mapping(self) -> None:
        import importlib.util

        path = next((API_ROOT / "migrations" / "versions").glob(f"{SPLIT_REVISION}_*.py"))
        spec = importlib.util.spec_from_file_location("split_mig", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        from fde_api.forward.scheduler import JobStatus

        for st in JobStatus:
            assert st.value in mod.LEGACY_STATE_MAP, f"no mapping for JobStatus.{st.name}"

    def test_every_outcome_value_is_reachable(self) -> None:
        import importlib.util

        path = next((API_ROOT / "migrations" / "versions").glob(f"{SPLIT_REVISION}_*.py"))
        spec = importlib.util.spec_from_file_location("split_mig2", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        from fde_api.forward.handlers import DomainState, Outcome

        produced_outcomes = {v[0] for v in mod.LEGACY_STATE_MAP.values()}
        produced_domains = {v[1] for v in mod.LEGACY_STATE_MAP.values()}
        assert produced_outcomes <= {o.value for o in Outcome}
        assert produced_domains <= {d.value for d in DomainState}
