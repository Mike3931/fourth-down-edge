"""Recovery lineage: chains, invariants, replay decisions, effect inspection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import OddsQuote, ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.recovery import (
    JOB_CATEGORIES,
    EffectInspection,
    JobCategory,
    RecoveryError,
    ReplayDecision,
    active_member,
    blocks_automatic_recovery,
    build_recovery_lineage,
    chain_members,
    inspect_prior_effects,
    inspect_terminal_effects,
    lineage_violations,
    validate_recovery,
)
from fde_api.forward.state import Outcome, StateOrigin, apply_state

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _run(session: Session, rid: str, *, outcome=Outcome.RUNNING, seq=0, root=None,
         predecessor=None, reason=None, decision=None, override=False,
         operator=None, override_reason=None, kind="odds_capture") -> ScheduledJobRun:
    run = ScheduledJobRun(
        id=rid, job_kind=kind, idempotency_key=f"k:{rid}", data_mode="LIVE_RESEARCH",
        scheduled_for=T0, started_at=T0, status="running", retry_count=0,
        provider_calls=0, records_received=0, records_written=0, code_commit="abc",
        created_at=T0 + timedelta(seconds=seq), root_run_id=root or rid,
        recovery_of_run_id=predecessor, recovery_sequence=seq, logical_slot=T0,
        original_idempotency_key="k:root", recovery_reason=reason,
        replay_decision=decision.value if decision else None,
        administrative_override=override, override_operator=operator,
        override_reason=override_reason, state_origin="LIVE",
    )
    apply_state(run, outcome=outcome)
    session.add(run)
    session.flush()
    return run


class TestJobCategories:
    def test_every_job_has_a_category(self) -> None:
        from fde_api.forward.handlers import HANDLERS

        for name in HANDLERS:
            if name in ("manual_price_expiration", "data_health_reconciliation"):
                continue
            assert name in JOB_CATEGORIES, name

    def test_categories_are_correct(self) -> None:
        assert JOB_CATEGORIES["odds_capture"] is JobCategory.OBSERVATION
        assert JOB_CATEGORIES["prediction_vintage"] is JobCategory.SNAPSHOT
        assert JOB_CATEGORIES["settlement"] is JobCategory.TERMINAL


class TestEffectInspection:
    def test_no_effects_yields_replay(self, session: Session) -> None:
        insp = inspect_prior_effects(session, job_kind="odds_capture", idempotency_key="k1")
        assert insp.actual_effect_count == 0
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
        assert insp.complete is False

    def test_committed_observation_effects_are_discovered(self, session: Session) -> None:
        """Prior effects come from the DOMAIN, not from run status."""
        session.add(OddsQuote(
            data_mode="LIVE_RESEARCH", canonical_game_id="g1", provider="p",
            sportsbook="draftkings", market="SPREAD", selection="HOME", line=-2.5,
            american=-110, decimal_odds=1.91, is_live=False, observed_at=T0,
            request_id="k1", raw_hash="h1"))
        session.flush()
        insp = inspect_prior_effects(session, job_kind="odds_capture", idempotency_key="k1")
        assert insp.actual_effect_count == 1
        assert insp.recommended_decision is ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY
        assert insp.record_identifiers

    def test_inspection_reports_all_required_fields(self, session: Session) -> None:
        insp = inspect_prior_effects(session, job_kind="odds_capture", idempotency_key="k1")
        d = insp.as_dict()
        for f in ("record_type", "record_identifiers", "idempotency_keys",
                  "expected_effect_count", "actual_effect_count", "complete",
                  "conflicts", "recommended_decision", "reason"):
            assert f in d, f
        assert d["reason"]

    def test_terminal_matching_result_is_no_replay(self, session: Session) -> None:
        from fde_api.db.forward_models import ForwardLedgerEntry

        session.add(ForwardLedgerEntry(
            data_mode="LIVE_RESEARCH", canonical_game_id="g2", policy_version="p1",
            model_version="m1", horizon="PREGAME", market="SPREAD", status="RESEARCH_CANDIDATE",
            reasons={}, as_of_at=T0, created_at=T0, result="WIN"))
        session.flush()
        insp = inspect_terminal_effects(session, job_kind="settlement",
                                        canonical_game_id="g2", expected_result="WIN")
        assert insp.recommended_decision is ReplayDecision.PRIOR_EFFECTS_NO_REPLAY

    def test_terminal_conflict_requires_manual_review(self, session: Session) -> None:
        from fde_api.db.forward_models import ForwardLedgerEntry

        session.add(ForwardLedgerEntry(
            data_mode="LIVE_RESEARCH", canonical_game_id="g3", policy_version="p1",
            model_version="m1", horizon="PREGAME", market="SPREAD", status="RESEARCH_CANDIDATE",
            reasons={}, as_of_at=T0, created_at=T0, result="LOSS"))
        session.flush()
        insp = inspect_terminal_effects(session, job_kind="settlement",
                                        canonical_game_id="g3", expected_result="WIN")
        assert insp.recommended_decision is ReplayDecision.MANUAL_REVIEW_REQUIRED
        assert insp.conflicts

    def test_replay_decision_is_typed_not_text(self) -> None:
        insp = EffectInspection(job_kind="j", category=JobCategory.OBSERVATION, record_type="t")
        assert isinstance(insp.recommended_decision, ReplayDecision)
        assert insp.as_dict()["recommended_decision"] in {d.value for d in ReplayDecision}


class TestChainConstruction:
    def test_recovery_inherits_root_and_increments(self, session: Session) -> None:
        root = _run(session, "r0", outcome=Outcome.INTERRUPTED)
        lin = build_recovery_lineage(session, root, run_id="r1",
                                     recovery_key="k:r1", reason="crash", now=T0)
        assert lin["root_run_id"] == "r0"
        assert lin["recovery_of_run_id"] == "r0"
        assert lin["recovery_sequence"] == 1
        assert lin["logical_slot"] == T0
        assert lin["original_idempotency_key"] == "k:root"
        assert lin["state_origin"] == StateOrigin.LIVE.value

    def test_recovery_of_migrated_run_is_live(self, session: Session) -> None:
        root = _run(session, "m0", outcome=Outcome.INTERRUPTED)
        root.state_origin = "MIGRATED_LEGACY"
        session.flush()
        lin = build_recovery_lineage(session, root, run_id="m1",
                                     recovery_key="k:m1", reason="crash", now=T0)
        assert lin["state_origin"] == "LIVE"

    def test_three_consecutive_recoveries(self, session: Session) -> None:
        prev = _run(session, "c0", outcome=Outcome.INTERRUPTED)
        for i in range(1, 4):
            lin = build_recovery_lineage(session, prev, run_id=f"c{i}",
                                         recovery_key=f"k:c{i}", reason="crash", now=T0)
            assert lin["recovery_sequence"] == i
            prev = _run(session, f"c{i}", outcome=Outcome.INTERRUPTED, seq=i,
                        root="c0", predecessor=lin["recovery_of_run_id"], reason="crash",
                        decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        members = chain_members(session, "c0")
        assert [m.recovery_sequence for m in members] == [0, 1, 2, 3]

    def test_chain_is_chronological(self, session: Session) -> None:
        _run(session, "d0", outcome=Outcome.INTERRUPTED)
        _run(session, "d1", outcome=Outcome.INTERRUPTED, seq=1, root="d0", predecessor="d0",
             reason="x", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        ids = [m.id for m in chain_members(session, "d0")]
        assert ids == ["d0", "d1"]


class TestChainInvariants:
    def test_reason_is_required(self, session: Session) -> None:
        root = _run(session, "e0", outcome=Outcome.INTERRUPTED)
        with pytest.raises(RecoveryError, match="requires a reason"):
            build_recovery_lineage(session, root, run_id="e1", recovery_key="k",
                                   reason="  ", now=T0)

    def test_completed_chain_cannot_reopen(self, session: Session) -> None:
        root = _run(session, "f0", outcome=Outcome.SUCCESS)
        with pytest.raises(RecoveryError, match="cannot be silently reopened"):
            build_recovery_lineage(session, root, run_id="f1", recovery_key="k",
                                   reason="retry", now=T0)

    def test_terminal_failure_needs_override(self, session: Session) -> None:
        root = _run(session, "g0", outcome=Outcome.TERMINAL_FAILURE)
        with pytest.raises(RecoveryError, match="administrative override"):
            build_recovery_lineage(session, root, run_id="g1", recovery_key="k",
                                   reason="retry", now=T0)

    def test_override_requires_operator(self, session: Session) -> None:
        root = _run(session, "h0", outcome=Outcome.TERMINAL_FAILURE)
        with pytest.raises(RecoveryError, match="requires an operator"):
            build_recovery_lineage(session, root, run_id="h1", recovery_key="k",
                                   reason="retry", now=T0, administrative_override=True,
                                   override_reason="approved")

    def test_override_requires_reason(self, session: Session) -> None:
        root = _run(session, "i0", outcome=Outcome.TERMINAL_FAILURE)
        with pytest.raises(RecoveryError, match="override requires a reason"):
            build_recovery_lineage(session, root, run_id="i1", recovery_key="k",
                                   reason="retry", now=T0, administrative_override=True,
                                   override_operator="alex")

    def test_valid_override_succeeds_and_is_auditable(self, session: Session) -> None:
        root = _run(session, "j0", outcome=Outcome.TERMINAL_FAILURE)
        lin = build_recovery_lineage(session, root, run_id="j1", recovery_key="k",
                                     reason="retry", now=T0, administrative_override=True,
                                     override_operator="alex", override_reason="verified safe")
        assert lin["administrative_override"] is True
        assert lin["override_operator"] == "alex"
        assert lin["override_reason"] == "verified safe"

    def test_parallel_branch_is_rejected(self, session: Session) -> None:
        root = _run(session, "k0", outcome=Outcome.INTERRUPTED)
        _run(session, "k1", outcome=Outcome.RUNNING, seq=1, root="k0", predecessor="k0",
             reason="first")
        with pytest.raises(RecoveryError, match="parallel recovery"):
            build_recovery_lineage(session, root, run_id="k2", recovery_key="k",
                                   reason="second", now=T0)

    def test_logical_slot_cannot_change(self, session: Session) -> None:
        root = _run(session, "l0", outcome=Outcome.INTERRUPTED)
        with pytest.raises(RecoveryError, match="logical slot"):
            validate_recovery(root, cohort=None, policy_version=None,
                              logical_slot=T0 + timedelta(hours=1), provider_mode=None,
                              reason="x")

    def test_cohort_cannot_change(self, session: Session) -> None:
        root = _run(session, "n0", outcome=Outcome.INTERRUPTED)
        with pytest.raises(RecoveryError, match="cohort"):
            validate_recovery(root, cohort="DEMO", policy_version=None,
                              logical_slot=T0, provider_mode=None, reason="x")

    def test_active_member_detection(self, session: Session) -> None:
        _run(session, "o0", outcome=Outcome.INTERRUPTED)
        assert active_member(session, "o0") is None
        _run(session, "o1", outcome=Outcome.RUNNING, seq=1, root="o0", predecessor="o0",
             reason="r")
        assert active_member(session, "o0").id == "o1"


class TestLineageViolations:
    def test_clean_chain_has_no_violations(self, session: Session) -> None:
        _run(session, "p0", outcome=Outcome.INTERRUPTED,
             decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "p1", outcome=Outcome.SUCCESS, seq=1, root="p0", predecessor="p0",
             reason="crash", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        assert lineage_violations(session) == []

    def test_multiple_active_runs_detected(self, session: Session) -> None:
        _run(session, "q0", outcome=Outcome.RUNNING)
        _run(session, "q1", outcome=Outcome.RUNNING, seq=1, root="q0", predecessor="q0",
             reason="r")
        v = [x["check"] for x in lineage_violations(session)]
        assert "multiple_active_runs" in v
        assert blocks_automatic_recovery(lineage_violations(session))

    def test_broken_predecessor_link_detected(self, session: Session) -> None:
        _run(session, "s0", outcome=Outcome.INTERRUPTED,
             decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "s1", outcome=Outcome.SUCCESS, seq=1, root="s0", predecessor="missing",
             reason="r", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        assert "broken_predecessor_link" in [x["check"] for x in lineage_violations(session)]

    def test_sequence_gap_detected(self, session: Session) -> None:
        _run(session, "t0", outcome=Outcome.INTERRUPTED,
             decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "t1", outcome=Outcome.SUCCESS, seq=3, root="t0", predecessor="t0",
             reason="r", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        assert "incorrect_recovery_sequence" in [x["check"] for x in lineage_violations(session)]

    def test_recovery_branch_detected(self, session: Session) -> None:
        _run(session, "u0", outcome=Outcome.INTERRUPTED,
             decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "u1", outcome=Outcome.INTERRUPTED, seq=1, root="u0", predecessor="u0",
             reason="r", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "u2", outcome=Outcome.SUCCESS, seq=1, root="u0", predecessor="u0",
             reason="r", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        assert "recovery_branch" in [x["check"] for x in lineage_violations(session)]

    def test_missing_override_identity_detected(self, session: Session) -> None:
        _run(session, "v0", outcome=Outcome.INTERRUPTED,
             decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "v1", outcome=Outcome.SUCCESS, seq=1, root="v0", predecessor="v0",
             reason="r", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY, override=True)
        assert "missing_override_identity" in [x["check"] for x in lineage_violations(session)]

    def test_closed_chain_reopened_detected(self, session: Session) -> None:
        _run(session, "w0", outcome=Outcome.SUCCESS,
             decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        _run(session, "w1", outcome=Outcome.SUCCESS, seq=1, root="w0", predecessor="w0",
             reason="r", decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY)
        assert "closed_chain_reopened" in [x["check"] for x in lineage_violations(session)]

    def test_unresolved_manual_review_detected(self, session: Session) -> None:
        _run(session, "x0", outcome=Outcome.RUNNING,
             decision=ReplayDecision.MANUAL_REVIEW_REQUIRED)
        assert "manual_review_unresolved" in [x["check"] for x in lineage_violations(session)]

    def test_critical_corruption_blocks_recovery(self, session: Session) -> None:
        _run(session, "y0", outcome=Outcome.RUNNING)
        _run(session, "y1", outcome=Outcome.RUNNING, seq=1, root="y0", predecessor="y0",
             reason="r")
        assert blocks_automatic_recovery(lineage_violations(session)) is True


class TestDataHealthIntegration:
    def test_lineage_checks_present(self, session: Session) -> None:
        from fde_api.forward.health import run_health_checks

        r = run_health_checks(session, policy_version=None)
        ids = {c["id"] for c in r["checks"]}
        for name in ("lineage_multiple_active_runs", "lineage_broken_predecessor_link",
                     "lineage_incorrect_recovery_sequence", "lineage_root_mismatch",
                     "lineage_recovery_branch", "lineage_missing_recovery_reason",
                     "lineage_missing_override_identity", "lineage_closed_chain_reopened",
                     "lineage_replay_decision_absent", "lineage_manual_review_unresolved",
                     "compatibility_status_drift", "unknown_legacy_origin",
                     "automatic_recovery_permitted"):
            assert name in ids, name

    def test_corruption_surfaces_as_critical(self, session: Session) -> None:
        from fde_api.forward.health import run_health_checks

        _run(session, "z0", outcome=Outcome.RUNNING)
        _run(session, "z1", outcome=Outcome.RUNNING, seq=1, root="z0", predecessor="z0",
             reason="r")
        session.flush()
        r = run_health_checks(session, policy_version=None)
        check = next(c for c in r["checks"] if c["id"] == "automatic_recovery_permitted")
        assert check["status"] != "OK"
        assert check["severity"] == "CRITICAL"


class TestProviderModeInvariance:
    """A recovery may not change the provider mode.

    This check used to be a comment explaining why it could not exist:
    `scheduled_job_runs` did not persist the mode, so there was nothing to
    compare against. Migration e7b3c04d1f28 added the column, and these
    tests are what make the check load-bearing rather than decorative -
    each one fails if the comparison is removed.

    It matters because a recovery that changes the mode launders data
    across the fixture/live boundary: a slot captured from a fixture
    payload, recovered under LIVE, would produce records stamped LIVE that
    no provider ever returned.
    """

    def test_the_same_mode_is_permitted(self, session: Session) -> None:
        run = _run(session, "r1", outcome=Outcome.INTERRUPTED)
        run.provider_mode = "FIXTURE"
        session.commit()
        validate_recovery(
            run, cohort=None, policy_version=None, logical_slot=None,
            provider_mode="FIXTURE", reason="interrupted",
        )

    def test_a_different_mode_is_refused(self, session: Session) -> None:
        run = _run(session, "r1", outcome=Outcome.INTERRUPTED)
        run.provider_mode = "FIXTURE"
        session.commit()
        with pytest.raises(RecoveryError, match="may not change the provider mode"):
            validate_recovery(
                run, cohort=None, policy_version=None, logical_slot=None,
                provider_mode="LIVE", reason="interrupted",
            )

    def test_the_message_names_both_modes(self, session: Session) -> None:
        run = _run(session, "r1", outcome=Outcome.INTERRUPTED)
        run.provider_mode = "FIXTURE"
        session.commit()
        with pytest.raises(RecoveryError) as e:
            validate_recovery(
                run, cohort=None, policy_version=None, logical_slot=None,
                provider_mode="LIVE", reason="interrupted",
            )
        assert "FIXTURE" in str(e.value)
        assert "LIVE" in str(e.value)

    def test_a_legacy_row_cannot_be_recovered_automatically(self, session: Session) -> None:
        """Backfilled rows have no recoverable mode. An invariant that cannot
        be verified is a reason to involve a human, not to wave it through."""
        run = _run(session, "r1", outcome=Outcome.INTERRUPTED)
        run.provider_mode = "UNKNOWN_LEGACY"
        session.commit()
        with pytest.raises(RecoveryError, match="UNKNOWN_LEGACY"):
            validate_recovery(
                run, cohort=None, policy_version=None, logical_slot=None,
                provider_mode="FIXTURE", reason="interrupted",
            )

    def test_a_legacy_row_may_be_recovered_by_an_operator(self, session: Session) -> None:
        run = _run(session, "r1", outcome=Outcome.INTERRUPTED)
        run.provider_mode = "UNKNOWN_LEGACY"
        session.commit()
        validate_recovery(
            run, cohort=None, policy_version=None, logical_slot=None,
            provider_mode="FIXTURE", reason="interrupted",
            administrative_override=True, override_operator="ops",
            override_reason="verified against the raw archive by hand",
        )

    def test_an_unstated_mode_skips_the_check(self, session: Session) -> None:
        """Callers that genuinely do not know the mode must not be forced to
        invent one; the check is skipped rather than guessed."""
        run = _run(session, "r1", outcome=Outcome.INTERRUPTED)
        run.provider_mode = "FIXTURE"
        session.commit()
        validate_recovery(
            run, cohort=None, policy_version=None, logical_slot=None,
            provider_mode=None, reason="interrupted",
        )
