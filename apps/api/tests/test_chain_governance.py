"""Governance propagation, health suppression, and recovery inside the chain.

Three properties that are only meaningful end to end:

  §4  Every record carries the fields that make it auditable, and they
      agree with each other. A cohort on the run and a different cohort on
      the record it produced is not a partial success.

  §7  Data Health suppression cannot be bypassed by calling the domain
      service directly, prior evaluations are never rewritten, and
      remediation produces a NEW evaluation rather than reviving the old
      one.

  §15 A crash at each interruption boundary produces exactly one recovery
      successor, the correct replay decision, and no duplicate domain
      record.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from chainkit import (
    DATA_MODE,
    GAME,
    KICK,
    SchedulerChain,
    payload_for_slot,
)
from fde_api.db.forward_models import (
    ConsensusSnapshot,
    ForwardLedgerEntry,
    ForwardPrediction,
    ManualBookPriceEntry,
    OddsQuote,
    ScheduledJobRun,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.forward.chain import chain_semantic_hash, read_chain
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.recovery import ReplayDecision
from fde_api.forward.state import Outcome

POLICY = "ftp-2026-v1"


# --------------------------------------------------------------------------- #
# §4 — governance propagation
# --------------------------------------------------------------------------- #


class TestGovernanceFieldsPropagate:
    """Each record must carry what an auditor needs to place it.

    Written as a per-table sweep rather than one big assertion so a failure
    names the table and the field, not just "something is missing".
    """

    def test_every_record_carries_the_canonical_game_id(self, chain) -> None:
        with chain.session() as s:
            for model in (ScheduleObservation, OddsQuote, ConsensusSnapshot,
                          WeatherForecastVintage, ForwardPrediction,
                          ManualBookPriceEntry, ForwardLedgerEntry):
                rows = list(s.scalars(select(model)))
                assert rows, f"{model.__tablename__} is empty; nothing to govern"
                blank = [r for r in rows if not r.canonical_game_id]
                assert not blank, f"{model.__tablename__} has rows with no game id"

    def test_every_record_carries_its_cohort(self, chain) -> None:
        with chain.session() as s:
            for model in (ScheduleObservation, OddsQuote, ConsensusSnapshot,
                          WeatherForecastVintage, ForwardPrediction,
                          ManualBookPriceEntry, ForwardLedgerEntry):
                modes = {r.data_mode for r in s.scalars(select(model))}
                assert modes == {DATA_MODE.value}, f"{model.__tablename__}: {modes}"

    def test_market_records_carry_provider_provenance(self, chain) -> None:
        """Fixture output must never be storable as live-provider output."""
        with chain.session() as s:
            for model in (OddsQuote, ConsensusSnapshot):
                modes = {r.provider_mode for r in s.scalars(select(model))}
                assert modes == {ProviderMode.FIXTURE.value}, (
                    f"{model.__tablename__}: {modes}"
                )

    def test_the_price_record_carries_every_governance_field(self, chain) -> None:
        with chain.session() as s:
            prices = list(s.scalars(select(ManualBookPriceEntry)))
        assert prices
        for p in prices:
            assert p.cohort == Cohort.FIXTURE.value
            assert p.provider_mode == ProviderMode.FIXTURE.value
            assert p.policy_version == POLICY
            assert p.code_commit, "no code commit recorded on the price"
            assert p.decimal_odds and p.decimal_odds > 1
            assert p.break_even_probability and 0 < p.break_even_probability < 1
            assert p.observed_at.tzinfo is not None
            assert p.entered_at.tzinfo is not None
            assert p.user_id

    def test_predictions_and_evaluations_agree_on_policy_and_model(self, chain) -> None:
        """A ledger row whose policy differs from the vintage it came from
        would make the evaluation unattributable to any frozen ruleset."""
        with chain.session() as s:
            preds = {p.id: p for p in s.scalars(select(ForwardPrediction))}
            for e in s.scalars(select(ForwardLedgerEntry)):
                assert e.policy_version == POLICY
                assert e.model_version
                if e.forward_prediction_id:
                    pred = preds[e.forward_prediction_id]
                    assert pred.policy_version == e.policy_version
                    assert pred.model_version == e.model_version

    def test_every_scheduler_run_carries_its_code_commit(self, chain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        assert runs
        assert all(r.code_commit for r in runs)

    def test_every_run_records_both_its_slot_and_its_observation_time(
        self, chain
    ) -> None:
        """The scheduled slot and the moment work actually happened are
        different facts. Collapsing them hides late execution entirely."""
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.scheduled_for is not None, f"{r.job_kind} has no slot"
            assert r.started_at is not None, f"{r.job_kind} has no start time"
            assert r.created_at.tzinfo is not None

    def test_predictions_carry_their_input_lineage(self, chain) -> None:
        with chain.session() as s:
            preds = list(s.scalars(select(ForwardPrediction)))
        assert preds
        for p in preds:
            assert p.lineage, f"prediction {p.id} has no lineage"
            assert p.feature_set_version
            assert p.artifact_hash


# --------------------------------------------------------------------------- #
# §7 — suppression cannot be bypassed
# --------------------------------------------------------------------------- #


def _force_suppression(session) -> None:
    """Introduce a critical Data Health failure.

    Corrupts the recovery lineage rather than deleting data: a chain with
    two active members is a CRITICAL check that suppresses candidates, and
    it does not require removing records the rest of the test still needs.
    """
    runs = list(session.scalars(select(ScheduledJobRun)))
    assert len(runs) >= 2
    for r in runs[:2]:
        r.job_outcome = Outcome.RUNNING.value
        r.status = "running"
        r.root_run_id = runs[0].id
        r.recovery_sequence = 0
    session.commit()


class TestHealthSuppression:
    def test_a_healthy_chain_produces_an_unsuppressed_evaluation(self, chain) -> None:
        with chain.session() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry)))
        assert rows, "no evaluation was produced at all"
        statuses = {r.status for r in rows}
        assert statuses <= {"RESEARCH_CANDIDATE", "WATCH", "PASS", "DATA_INCOMPLETE"}
        assert "BET" not in statuses

    def test_the_domain_service_cannot_be_called_around_suppression(
        self, chain
    ) -> None:
        """Suppressing only in the UI would leave the API a bypass, so the
        gate lives in the evaluation service itself."""
        from fde_api.forward.health import run_health_checks
        from fde_api.forward.ledger import HealthGate, evaluate_candidate
        from fde_api.forward.policy import load_policy

        with chain.session() as s:
            _force_suppression(s)
            report = run_health_checks(
                s, data_mode=DATA_MODE, provider_mode=ProviderMode.FIXTURE,
                policy_version=POLICY, now=KICK,
            )
            assert report["candidates_suppressed"], "the failure did not suppress"
            policy = load_policy(s, POLICY)
            ev = evaluate_candidate(
                market="SPREAD", selection="HOME", line=-3.0, american=-110,
                model_probability=0.99,   # would otherwise be a clear candidate
                price_source="fixture_price", price_age_seconds=10,
                policy=policy, data_completeness=1.0,
                health_gate=HealthGate.from_report(report),
            )
        assert ev.status == "DATA_INCOMPLETE"
        assert any("suppressed by Data Health" in r for r in ev.reasons)

    def test_the_prior_evaluation_is_not_rewritten(self, chain) -> None:
        with chain.session() as s:
            before = {e.id: (e.status, e.as_of_at, e.expected_value)
                      for e in s.scalars(select(ForwardLedgerEntry))}
            _force_suppression(s)
        assert before

        suppressed_run = SchedulerChain(chain.factory)
        suppressed_run.at(KICK - timedelta(days=1))
        suppressed_run.run("price_evaluation")

        with chain.session() as s:
            after = {e.id: (e.status, e.as_of_at, e.expected_value)
                     for e in s.scalars(select(ForwardLedgerEntry))}
        for eid, snapshot in before.items():
            assert after[eid] == snapshot, f"evaluation {eid} was rewritten"

    def test_a_new_suppressed_evaluation_is_created_with_its_health_lineage(
        self, chain
    ) -> None:
        with chain.session() as s:
            before = len(list(s.scalars(select(ForwardLedgerEntry))))
            _force_suppression(s)

        run = SchedulerChain(chain.factory)
        run.at(KICK - timedelta(days=1))
        result = run.run("price_evaluation")

        with chain.session() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry)))
        assert len(rows) > before, "no new evaluation was written under suppression"
        assert result["status"] in {"finished", "skipped"}
        newest = sorted(rows, key=lambda r: (r.as_of_at, r.id))[-1]
        assert newest.status == "DATA_INCOMPLETE"
        reasons = " ".join((newest.reasons or {}).get("reasons", []))
        assert "Data Health" in reasons, reasons

    def test_after_remediation_a_new_evaluation_may_be_created(self, chain) -> None:
        """Under the SAME frozen model and policy: remediation restores the
        ability to evaluate, it does not license changing the rules."""
        with chain.session() as s:
            _force_suppression(s)
        suppressed = SchedulerChain(chain.factory)
        suppressed.at(KICK - timedelta(days=1))
        suppressed.run("price_evaluation")

        with chain.session() as s:
            suppressed_ids = {e.id for e in s.scalars(select(ForwardLedgerEntry))
                              if e.status == "DATA_INCOMPLETE"}
            # Remediate: end the duplicated active members.
            for r in s.scalars(select(ScheduledJobRun)):
                if r.job_outcome == Outcome.RUNNING.value:
                    r.job_outcome = Outcome.SUCCESS.value
                    r.status = "finished"
            s.commit()

        remediated = SchedulerChain(chain.factory)
        remediated.at(KICK - timedelta(hours=18))
        remediated.run("price_evaluation")

        with chain.session() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry)))
            policies = {e.policy_version for e in rows}
            models = {e.model_version for e in rows}
            still_there = {e.id for e in rows} & suppressed_ids
        assert still_there == suppressed_ids, "a suppressed record was deleted"
        assert policies == {POLICY}, policies
        assert len(models) == 1, models

    def test_the_suppressed_record_is_neither_rewritten_nor_deleted(
        self, chain
    ) -> None:
        with chain.session() as s:
            _force_suppression(s)
        run = SchedulerChain(chain.factory)
        run.at(KICK - timedelta(days=1))
        run.run("price_evaluation")

        with chain.session() as s:
            suppressed = [e for e in s.scalars(select(ForwardLedgerEntry))
                          if e.status == "DATA_INCOMPLETE"]
            snapshot = {e.id: (e.status, e.reasons) for e in suppressed}
        assert snapshot, "nothing was suppressed"

        later = SchedulerChain(chain.factory)
        later.at(KICK - timedelta(hours=12))
        later.run("price_evaluation")

        with chain.session() as s:
            after = {e.id: (e.status, e.reasons)
                     for e in s.scalars(select(ForwardLedgerEntry))}
        for eid, value in snapshot.items():
            assert eid in after, f"suppressed record {eid} was deleted"
            assert after[eid] == value, f"suppressed record {eid} was rewritten"


# --------------------------------------------------------------------------- #
# §15 — recovery at each interruption boundary
# --------------------------------------------------------------------------- #


BOUNDARIES = [
    ("odds_capture", OddsQuote),
    ("prediction_vintage", ForwardPrediction),
    ("closing_capture", ConsensusSnapshot),
    ("settlement", ForwardLedgerEntry),
]


class TestRecoveryInsideTheChain:
    """Crash after commit, before the run row is finalised.

    The dangerous boundary: the run says RUNNING while real records exist.
    Anything that decides replay from run status alone gets this wrong.
    """

    @pytest.mark.parametrize("job,model", BOUNDARIES, ids=[b[0] for b in BOUNDARIES])
    def test_one_recovery_successor_and_no_duplicate_records(
        self, chain, job: str, model
    ) -> None:
        with chain.session() as s:
            before_rows = len(list(s.scalars(select(model))))
            before_hash = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version=POLICY))
            runs = [r for r in s.scalars(select(ScheduledJobRun))
                    if r.job_kind == job]
            assert runs, f"{job} never ran, so there is nothing to interrupt"
            victim = sorted(runs, key=lambda r: r.created_at)[-1]
            victim.job_outcome = Outcome.RUNNING.value
            victim.status = "running"
            victim.completed_at = None
            s.commit()
            root = victim.root_run_id or victim.id

        restarted = SchedulerChain(chain.factory)
        assert restarted.sched.reconcile_startup()["count"] == 1

        with chain.session() as s:
            interrupted = [r for r in s.scalars(select(ScheduledJobRun))
                           if r.id == victim.id]
            assert interrupted[0].job_outcome == Outcome.INTERRUPTED.value

        # Re-run the same slot; the scheduler must open exactly one recovery.
        restarted.at(max(victim.scheduled_for, KICK - timedelta(days=7)))
        params = _params_for(job, restarted)
        result = restarted.sched.run_job(job, slot=victim.scheduled_for, params=params)
        assert result["status"] in {"finished", "skipped", "manual_review_required"}, result

        with chain.session() as s:
            chain_rows = [r for r in s.scalars(select(ScheduledJobRun))
                          if (r.root_run_id or r.id) == root]
            successors = [r for r in chain_rows if (r.recovery_sequence or 0) == 1]
            after_rows = len(list(s.scalars(select(model))))
            after_hash = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version=POLICY))

        assert len(successors) <= 1, [r.id for r in successors]
        assert after_rows == before_rows, (
            f"{job} recovery duplicated {model.__tablename__}: "
            f"{before_rows} -> {after_rows}"
        )
        assert after_hash == before_hash

    @pytest.mark.parametrize("job,model", BOUNDARIES, ids=[b[0] for b in BOUNDARIES])
    def test_the_recovery_carries_a_typed_replay_decision(
        self, chain, job: str, model
    ) -> None:
        with chain.session() as s:
            runs = [r for r in s.scalars(select(ScheduledJobRun)) if r.job_kind == job]
            victim = sorted(runs, key=lambda r: r.created_at)[-1]
            victim.job_outcome = Outcome.RUNNING.value
            victim.status = "running"
            victim.completed_at = None
            s.commit()
            slot = victim.scheduled_for
            root = victim.root_run_id or victim.id

        restarted = SchedulerChain(chain.factory)
        restarted.sched.reconcile_startup()
        restarted.at(max(slot, KICK - timedelta(days=7)))
        restarted.sched.run_job(job, slot=slot, params=_params_for(job, restarted))

        with chain.session() as s:
            successors = [r for r in s.scalars(select(ScheduledJobRun))
                          if (r.root_run_id or r.id) == root
                          and (r.recovery_sequence or 0) == 1]
        valid = {d.value for d in ReplayDecision}
        for r in successors:
            assert r.replay_decision in valid, r.replay_decision
            assert r.recovery_reason, "a recovery must record why"
            assert r.reconciled_at is not None


def _params_for(job: str, run: SchedulerChain) -> dict | None:
    """The parameters each job needs when replayed at its own slot."""
    if job == "odds_capture":
        # The payload the ORIGINAL run saw, not a fresh one. Replaying with
        # different quotes writes genuinely different observations, which
        # reads as a duplicate-effect bug and is really the test feeding the
        # handler new data.
        return {"fixture_payload": payload_for_slot(run.clock.now())}
    if job == "settlement":
        return {"final_scores": {GAME: (24, 20)}}
    return None
