"""Authoritative state, compatibility projection, and origin enforcement.

`job_outcome` and `domain_state` are authoritative; `status` shadows the
outcome for pre-split readers. These tests prove the shadow can never
become the source of truth, and that UNKNOWN_LEGACY is unreachable for
live rows through ORM, bulk insert, or raw SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.state import (
    OUTCOME_TO_LEGACY_STATUS,
    DomainState,
    InvalidStateError,
    Outcome,
    StateOrigin,
    apply_state,
    completeness_metrics,
    detect_invalid_origin,
    detect_status_drift,
    project_status,
    reliability_metrics,
    repair_status_drift,
    validate_state,
)

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _run(session: Session, rid: str, **kw) -> ScheduledJobRun:
    base = {
        "id": rid, "job_kind": "odds_capture", "idempotency_key": f"k:{rid}",
        "data_mode": "LIVE_RESEARCH", "scheduled_for": T0, "started_at": T0,
        "status": "running", "retry_count": 0, "provider_calls": 0,
        "records_received": 0, "records_written": 0, "code_commit": "abc",
        "created_at": T0, "root_run_id": rid, "recovery_sequence": 0,
        "administrative_override": False, "state_origin": "LIVE",
    }
    base.update(kw)
    run = ScheduledJobRun(**base)
    session.add(run)
    session.flush()
    return run


class TestProjection:
    def test_every_outcome_has_exactly_one_legacy_value(self) -> None:
        assert set(OUTCOME_TO_LEGACY_STATUS) == set(Outcome)
        for o in Outcome:
            assert project_status(o) == OUTCOME_TO_LEGACY_STATUS[o]

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (Outcome.RUNNING, "running"),
            (Outcome.SUCCESS, "finished"),
            (Outcome.SUCCESS_WITH_WARNINGS, "finished"),
            (Outcome.SKIPPED, "skipped"),
            (Outcome.RETRYABLE_FAILURE, "failed"),
            (Outcome.TERMINAL_FAILURE, "dead_letter"),
            (Outcome.INTERRUPTED, "interrupted"),
        ],
    )
    def test_documented_projection(self, outcome, expected) -> None:
        assert project_status(outcome) == expected

    def test_projection_is_lossy_by_design(self) -> None:
        """Two outcomes share one legacy value, so `status` cannot be
        authoritative without losing information."""
        assert project_status(Outcome.SUCCESS) == project_status(Outcome.SUCCESS_WITH_WARNINGS)
        assert len(set(OUTCOME_TO_LEGACY_STATUS.values())) < len(Outcome)

    def test_outcome_transition_updates_status(self, session: Session) -> None:
        run = _run(session, "r1")
        apply_state(run, outcome=Outcome.RUNNING)
        assert run.status == "running"
        apply_state(run, outcome=Outcome.TERMINAL_FAILURE)
        assert run.job_outcome == "TERMINAL_FAILURE" and run.status == "dead_letter"

    def test_domain_transition_does_not_change_status(self, session: Session) -> None:
        run = _run(session, "r2")
        apply_state(run, outcome=Outcome.SUCCESS, domain_state=DomainState.COMPLETE)
        before = run.status
        apply_state(run, outcome=Outcome.SUCCESS, domain_state=DomainState.DATA_INCOMPLETE)
        assert run.status == before == "finished"
        assert run.domain_state == "DATA_INCOMPLETE"


class TestStatusIsNotAuthoritative:
    def test_direct_status_mutation_does_not_alter_authoritative_fields(
        self, session: Session
    ) -> None:
        run = _run(session, "r3")
        apply_state(run, outcome=Outcome.SUCCESS, domain_state=DomainState.COMPLETE)
        session.flush()
        session.execute(
            update(ScheduledJobRun).where(ScheduledJobRun.id == "r3").values(status="dead_letter")
        )
        session.flush()
        session.refresh(run)
        assert run.status == "dead_letter"  # the shadow was corrupted
        assert run.job_outcome == "SUCCESS"  # the truth is unchanged
        assert run.domain_state == "COMPLETE"

    def test_drift_is_detected(self, session: Session) -> None:
        run = _run(session, "r4")
        apply_state(run, outcome=Outcome.SUCCESS)
        session.flush()
        session.execute(
            update(ScheduledJobRun).where(ScheduledJobRun.id == "r4").values(status="skipped")
        )
        session.flush()
        drifts = detect_status_drift(session)
        assert [d.run_id for d in drifts] == ["r4"]
        assert drifts[0].stored_status == "skipped"
        assert drifts[0].expected_status == "finished"

    def test_no_drift_when_written_through_apply_state(self, session: Session) -> None:
        for i, o in enumerate(Outcome):
            apply_state(_run(session, f"ok{i}"), outcome=o)
        session.flush()
        assert detect_status_drift(session) == []

    def test_repair_restores_status(self, session: Session) -> None:
        run = _run(session, "r5")
        apply_state(run, outcome=Outcome.RETRYABLE_FAILURE)
        session.flush()
        session.execute(
            update(ScheduledJobRun).where(ScheduledJobRun.id == "r5").values(status="finished")
        )
        session.flush()
        out = repair_status_drift(session)
        session.refresh(run)
        assert out["repaired"] == 1
        assert run.status == "failed"

    def test_repair_never_changes_authoritative_fields(self, session: Session) -> None:
        run = _run(session, "r6")
        apply_state(run, outcome=Outcome.SKIPPED, domain_state=DomainState.NO_ELIGIBLE_RECORDS)
        session.flush()
        session.execute(
            update(ScheduledJobRun).where(ScheduledJobRun.id == "r6").values(status="finished")
        )
        session.flush()
        repair_status_drift(session)
        session.refresh(run)
        assert run.job_outcome == "SKIPPED"
        assert run.domain_state == "NO_ELIGIBLE_RECORDS"

    def test_dry_run_reports_without_writing(self, session: Session) -> None:
        run = _run(session, "r7")
        apply_state(run, outcome=Outcome.SUCCESS)
        session.flush()
        session.execute(
            update(ScheduledJobRun).where(ScheduledJobRun.id == "r7").values(status="skipped")
        )
        session.flush()
        out = repair_status_drift(session, dry_run=True)
        session.refresh(run)
        assert out["detected"] == 1 and out["repaired"] == 0
        assert run.status == "skipped"


class TestUnknownLegacyContainment:
    def test_service_layer_rejects_live_unknown(self, session: Session) -> None:
        run = _run(session, "u1")
        with pytest.raises(InvalidStateError, match="MIGRATED_LEGACY"):
            apply_state(run, outcome=Outcome.SUCCESS,
                        domain_state=DomainState.UNKNOWN_LEGACY,
                        state_origin=StateOrigin.LIVE)

    def test_validate_state_direct(self) -> None:
        with pytest.raises(InvalidStateError):
            validate_state(domain_state=DomainState.UNKNOWN_LEGACY, state_origin=StateOrigin.LIVE)
        validate_state(domain_state=DomainState.UNKNOWN_LEGACY,
                       state_origin=StateOrigin.MIGRATED_LEGACY)

    def test_orm_construction_is_blocked_by_the_database(self, session: Session) -> None:
        """The CHECK constraint stops what service validation never saw."""
        with pytest.raises(IntegrityError):
            _run(session, "u2", domain_state="UNKNOWN_LEGACY", state_origin="LIVE")
        session.rollback()

    def test_bulk_insert_is_blocked(self, session: Session) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                insert(ScheduledJobRun),
                [{
                    "id": "u3", "job_kind": "j", "idempotency_key": "k3",
                    "data_mode": "LIVE_RESEARCH", "scheduled_for": T0, "started_at": T0,
                    "status": "finished", "retry_count": 0, "provider_calls": 0,
                    "records_received": 0, "records_written": 0, "code_commit": "c",
                    "created_at": T0, "recovery_sequence": 0,
                    "administrative_override": False,
                    "domain_state": "UNKNOWN_LEGACY", "state_origin": "LIVE",
                }],
            )
        session.rollback()

    def test_raw_sql_is_blocked(self, session: Session) -> None:
        with pytest.raises(IntegrityError):
            session.execute(text(
                "INSERT INTO scheduled_job_runs (id, job_kind, idempotency_key, data_mode,"
                " scheduled_for, started_at, status, retry_count, provider_calls,"
                " records_received, records_written, code_commit, created_at,"
                " recovery_sequence, administrative_override, domain_state, state_origin)"
                " VALUES ('u4','j','k4','LIVE_RESEARCH',:t,:t,'finished',0,0,0,0,'c',:t,"
                " 0,0,'UNKNOWN_LEGACY','LIVE')"
            ), {"t": T0})
        session.rollback()

    def test_migrated_origin_is_permitted(self, session: Session) -> None:
        run = _run(session, "u5", domain_state="UNKNOWN_LEGACY",
                   state_origin="MIGRATED_LEGACY")
        assert run.domain_state == "UNKNOWN_LEGACY"
        assert detect_invalid_origin(session) == []

    def test_relabelling_a_migrated_row_live_is_blocked(self, session: Session) -> None:
        _run(session, "u6", domain_state="UNKNOWN_LEGACY", state_origin="MIGRATED_LEGACY")
        session.flush()
        with pytest.raises(IntegrityError):
            session.execute(
                update(ScheduledJobRun).where(ScheduledJobRun.id == "u6")
                .values(state_origin="LIVE")
            )
            session.flush()
        session.rollback()

    def test_invalid_origin_vocabulary_is_blocked(self, session: Session) -> None:
        with pytest.raises(IntegrityError):
            _run(session, "u7", state_origin="SOMETHING_ELSE")
        session.rollback()

    def test_new_runs_default_to_live(self, session: Session) -> None:
        run = _run(session, "u8")
        assert run.state_origin == "LIVE"


class TestMetricsSeparation:
    def _seed(self, session: Session) -> None:
        apply_state(_run(session, "m1"), outcome=Outcome.SUCCESS,
                    domain_state=DomainState.COMPLETE)
        apply_state(_run(session, "m2"), outcome=Outcome.SUCCESS_WITH_WARNINGS,
                    domain_state=DomainState.DATA_INCOMPLETE)
        apply_state(_run(session, "m3"), outcome=Outcome.RETRYABLE_FAILURE,
                    domain_state=DomainState.DATA_INCOMPLETE)
        _run(session, "m4", job_outcome="SUCCESS", status="finished",
             domain_state="UNKNOWN_LEGACY", state_origin="MIGRATED_LEGACY")
        session.flush()

    def test_reliability_uses_outcome_only(self, session: Session) -> None:
        self._seed(session)
        rel = reliability_metrics(session)
        assert rel["SUCCESS"] == 2  # includes the migrated row
        assert rel["RETRYABLE_FAILURE"] == 1
        assert "UNKNOWN_LEGACY" not in rel  # a domain value never appears here

    def test_completeness_uses_domain_only(self, session: Session) -> None:
        self._seed(session)
        comp = completeness_metrics(session)
        assert comp["by_state"]["COMPLETE"] == 1
        assert comp["by_state"]["DATA_INCOMPLETE"] == 2

    def test_unknown_legacy_has_its_own_bucket(self, session: Session) -> None:
        self._seed(session)
        comp = completeness_metrics(session)
        assert comp["migrated_unknown"] == 1
        assert comp["by_state"]["UNKNOWN_LEGACY"] == 1
        # Not folded into either side.
        assert comp["by_state"]["COMPLETE"] == 1
        assert comp["by_state"]["DATA_INCOMPLETE"] == 2


class TestInitialRunLineage:
    def test_scheduler_populates_lineage_before_execution(self) -> None:
        from fde_api.forward.scheduler import Scheduler

        lineage = Scheduler._initial_lineage("run_x", "key_x", T0)
        assert lineage["root_run_id"] == "run_x"
        assert lineage["recovery_of_run_id"] is None
        assert lineage["recovery_sequence"] == 0
        assert lineage["logical_slot"] == T0
        assert lineage["original_idempotency_key"] == "key_x"
        assert lineage["prior_effects_detected"] is False
        assert lineage["administrative_override"] is False
        assert lineage["replay_decision"] is None
        assert lineage["recovery_reason"] is None

    def test_every_required_field_is_present(self) -> None:
        from fde_api.forward.scheduler import Scheduler

        lineage = Scheduler._initial_lineage("r", "k", T0)
        for field in (
            "root_run_id", "recovery_of_run_id", "recovery_sequence", "logical_slot",
            "original_idempotency_key", "recovery_reason", "reconciled_at",
            "prior_effects_detected", "replay_decision", "administrative_override",
            "override_operator", "override_reason",
        ):
            assert field in lineage, field
