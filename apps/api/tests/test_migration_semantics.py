"""Migration semantics: no domain inference, UNKNOWN_LEGACY containment,
and the honest downgrade support decision.

The governing principle: a legacy execution status establishes the
execution OUTCOME exactly and says nothing about the DATA. "finished"
means the job ran, not that the domain was complete.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from tests.test_migration_upgrade import (
    CONSERVATIVE_ROWS,
    EXACT_ROWS,
    LEGACY_ROWS,
    PRIOR_REVISION,
    T0,
    _alembic,
    legacy_db,  # noqa: F401 - pytest fixture
)


class TestNoDomainInferenceFromExecutionStatus:
    def test_finished_without_metadata_is_unknown_not_complete(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT job_outcome, domain_state FROM scheduled_job_runs "
                     "WHERE id='run_legacy_0'")
            ).one()
        engine.dispose()
        assert row[0] == "SUCCESS"
        assert row[1] == "UNKNOWN_LEGACY", "COMPLETE must never be inferred from 'finished'"

    def test_no_concrete_domain_state_without_recorded_metadata(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, domain_state, error_summary FROM scheduled_job_runs")
            ).fetchall()
        engine.dispose()
        concrete = {"COMPLETE", "DATA_INCOMPLETE", "NOT_YET_AVAILABLE",
                    "NO_ELIGIBLE_RECORDS", "NOT_APPLICABLE", "STALE", "SUPPRESSED"}
        for _rid, domain, summary in rows:
            if domain in concrete:
                assert summary and f"domain_state={domain}" in summary, (
                    f"{domain} claimed without recorded metadata"
                )

    def test_exact_and_conservative_counts(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            exact = conn.execute(
                text("SELECT COUNT(*) FROM migration_audit WHERE exact = 1")).scalar()
            conservative = conn.execute(
                text("SELECT COUNT(*) FROM migration_audit WHERE exact = 0")).scalar()
        engine.dispose()
        assert exact == len(EXACT_ROWS) == 3
        assert conservative == len(CONSERVATIVE_ROWS) == 6


class TestUnknownLegacyIsMigrationOnly:
    def test_live_handler_result_rejects_it(self) -> None:
        from fde_api.forward.handlers import (
            DomainState,
            HandlerResult,
            IllegalDomainStateError,
            Outcome,
        )

        with pytest.raises(IllegalDomainStateError, match="migrated history"):
            HandlerResult(outcome=Outcome.SUCCESS, domain_state=DomainState.UNKNOWN_LEGACY)

    def test_live_domain_states_excludes_it(self) -> None:
        from fde_api.forward.handlers import LIVE_DOMAIN_STATES, DomainState

        assert DomainState.UNKNOWN_LEGACY not in LIVE_DOMAIN_STATES
        assert len(LIVE_DOMAIN_STATES) == len(DomainState) - 1

    def test_every_live_handler_emits_a_legal_state(self) -> None:
        """No handler hard-codes the migration-only state."""
        import inspect

        from fde_api.forward import handlers as h

        src = inspect.getsource(h)
        body = src.split("HANDLERS = {")[0]
        occurrences = body.count("DomainState.UNKNOWN_LEGACY")
        # Only the enum definition and the guard may mention it.
        assert occurrences <= 2, "a handler appears to emit UNKNOWN_LEGACY"

    def test_migrated_rows_may_hold_it(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM scheduled_job_runs "
                     "WHERE domain_state='UNKNOWN_LEGACY'")).scalar()
        engine.dispose()
        assert n == len(CONSERVATIVE_ROWS)

    def test_completeness_metrics_separate_unknowns(self, legacy_db: str) -> None:  # noqa: F811
        """Migrated unknowns are countable apart from complete/incomplete."""
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            buckets = dict(conn.execute(
                text("SELECT domain_state, COUNT(*) FROM scheduled_job_runs "
                     "GROUP BY domain_state")).fetchall())
        engine.dispose()
        assert buckets.get("UNKNOWN_LEGACY", 0) > 0
        # It is neither counted as complete nor as incomplete.
        assert buckets.get("COMPLETE", 0) + buckets.get("DATA_INCOMPLETE", 0) < sum(buckets.values())

    def test_reliability_metrics_use_outcome_not_domain(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            by_outcome = dict(conn.execute(
                text("SELECT job_outcome, COUNT(*) FROM scheduled_job_runs "
                     "GROUP BY job_outcome")).fetchall())
        engine.dispose()
        # Every row has a usable execution outcome even where the domain is unknown.
        assert sum(by_outcome.values()) == len(LEGACY_ROWS)
        assert "UNKNOWN_LEGACY" not in by_outcome


def _insert_post_upgrade_rows(url: str) -> list[tuple[str, str, str]]:
    combos = [
        ("run_new_0", "SUCCESS", "COMPLETE"),
        ("run_new_1", "SUCCESS_WITH_WARNINGS", "DATA_INCOMPLETE"),
        ("run_new_2", "SUCCESS_WITH_WARNINGS", "SUPPRESSED"),
        ("run_new_3", "SKIPPED", "NO_ELIGIBLE_RECORDS"),
        ("run_new_4", "SUCCESS_WITH_WARNINGS", "NOT_APPLICABLE"),
        ("run_new_5", "RETRYABLE_FAILURE", "DATA_INCOMPLETE"),
    ]
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for i, (rid, outcome, domain) in enumerate(combos):
            conn.execute(
                text(
                    "INSERT INTO scheduled_job_runs (id, job_kind, idempotency_key, data_mode,"
                    " scheduled_for, started_at, completed_at, status, job_outcome, domain_state,"
                    " retry_count, provider_calls, records_received, records_written,"
                    " code_commit, created_at, recovery_sequence, administrative_override,"
                    " root_run_id) "
                    "VALUES (:id,'odds_capture',:ik,'LIVE_RESEARCH',:t,:t,:t,'finished',"
                    " :o,:d,0,1,1,1,'e6a508a',:t,0,0,:id)"
                ),
                {"id": rid, "ik": f"new:{i}", "t": T0, "o": outcome, "d": domain},
            )
    engine.dispose()
    return combos


class TestPostUpgradeDowngradeRoundTrip:
    """Records created AFTER the split, not just migrated legacy rows."""

    def test_downgrade_drops_both_authoritative_columns(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        combos = _insert_post_upgrade_rows(legacy_db)
        assert _alembic(legacy_db, "downgrade", PRIOR_REVISION).returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            cols = [r[1] for r in conn.execute(
                text("PRAGMA table_info(scheduled_job_runs)")).fetchall()]
            n = conn.execute(
                text("SELECT COUNT(*) FROM scheduled_job_runs WHERE id LIKE 'run_new_%'")).scalar()
        engine.dispose()
        assert "domain_state" not in cols and "job_outcome" not in cols
        assert n == len(combos)  # rows survive; their meaning does not

    def test_round_trip_is_semantically_lossy(self, legacy_db: str) -> None:  # noqa: F811
        """Proof for the documented support decision."""
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        combos = _insert_post_upgrade_rows(legacy_db)
        assert _alembic(legacy_db, "downgrade", PRIOR_REVISION).returncode == 0
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            restored = dict(conn.execute(
                text("SELECT id, domain_state FROM scheduled_job_runs "
                     "WHERE id LIKE 'run_new_%'")).fetchall())
        engine.dispose()
        lost = [rid for rid, _o, domain in combos if restored[rid] != domain]
        assert lost, "if nothing is lost, the downgrade policy documentation must change"
        assert all(restored[rid] == "UNKNOWN_LEGACY" for rid in lost)

    def test_outcome_axis_is_recoverable(self, legacy_db: str) -> None:  # noqa: F811
        """Execution outcome survives because `status` projects it."""
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        _insert_post_upgrade_rows(legacy_db)
        assert _alembic(legacy_db, "downgrade", PRIOR_REVISION).returncode == 0
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT job_outcome FROM scheduled_job_runs "
                     "WHERE id LIKE 'run_new_%'")).fetchall()
        engine.dispose()
        assert all(r[0] == "SUCCESS" for r in rows)  # projected from status='finished'


class TestAuditIdempotencyAcrossCycles:
    def test_no_duplicate_indistinguishable_rows(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        assert _alembic(legacy_db, "downgrade", PRIOR_REVISION).returncode == 0
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            dupes = conn.execute(text(
                "SELECT revision, migration_cycle, table_name, record_id, COUNT(*) c "
                "FROM migration_audit GROUP BY 1,2,3,4 HAVING c > 1")).fetchall()
        engine.dispose()
        assert dupes == []

    def test_audit_count_matches_records_processed(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            runs = conn.execute(text("SELECT COUNT(*) FROM scheduled_job_runs")).scalar()
            audits = conn.execute(text("SELECT COUNT(*) FROM migration_audit")).scalar()
        engine.dispose()
        assert audits == runs

    def test_cycle_column_present_and_populated(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            cycles = {r[0] for r in conn.execute(
                text("SELECT DISTINCT migration_cycle FROM migration_audit")).fetchall()}
        engine.dispose()
        assert cycles == {1}

    def test_exact_and_conservative_remain_distinguishable(self, legacy_db: str) -> None:  # noqa: F811
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        assert _alembic(legacy_db, "downgrade", PRIOR_REVISION).returncode == 0
        assert _alembic(legacy_db, "upgrade", "head").returncode == 0
        engine = create_engine(legacy_db, future=True)
        with engine.connect() as conn:
            split = dict(conn.execute(
                text("SELECT exact, COUNT(*) FROM migration_audit GROUP BY exact")).fetchall())
        engine.dispose()
        assert split.get(1) == len(EXACT_ROWS)
        assert split.get(0) == len(CONSERVATIVE_ROWS)
