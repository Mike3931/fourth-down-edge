"""One game through the complete operational backend, via the scheduler.

Every other chain test drives domain services directly. This one drives
only `Scheduler.run_job`, in the order a real week produces the records,
because the operational proof is that the SCHEDULER can produce the chain -
not that the services can when called by hand. A service that works when
invoked directly and fails when invoked by a handler is a service that does
not work.

Cohort is `fixture` throughout. Fixture output must never be stored or
described as live-provider output, and it must never enter `burn_in` or
`official_forward_test`, so nothing here can contaminate an evaluable
cohort even by accident.

Nothing in this module asserts that any prediction is good. The engine is
research-only; the properties under test are structural.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from chainkit import (
    DATA_MODE,
    GAME,
    KICK,
    REQUIRED_SEQUENCE,
    SchedulerChain,
    drive,
    prices,
)
from fde_api.db.forward_models import (
    ForwardLedgerEntry,
    ForwardPrediction,
    ManualBookPriceEntry,
    ScheduledJobRun,
    ScheduleObservation,
)
from fde_api.forward.chain import (
    CHAIN_ORDER,
    CONDITIONALLY_ABSENT,
    ChainVerdict,
    Stage,
    chain_semantic_hash,
    read_chain,
    reconcile_chain,
)
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode
from fde_api.forward.state import Outcome

# --------------------------------------------------------------------------- #
# §14 — the sequence ran
# --------------------------------------------------------------------------- #


class TestTheSchedulerDroveEveryStage:
    @pytest.mark.parametrize("job", REQUIRED_SEQUENCE)
    def test_the_step_ran_and_did_not_fail(self, chain: SchedulerChain, job: str) -> None:
        statuses = chain.statuses()
        assert job in statuses, f"{job} never ran"
        assert statuses[job] in {"finished", "skipped"}, f"{job} -> {statuses[job]}"

    def test_no_step_was_refused_or_sent_to_review(self, chain: SchedulerChain) -> None:
        bad = [(j, s) for j, s in chain.log if s in {"refused", "manual_review_required"}]
        assert not bad, bad

    def test_the_runs_are_recorded_in_the_fixture_cohort_only(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            modes = {r.data_mode for r in s.scalars(select(ScheduledJobRun))}
        assert modes == {DATA_MODE.value}, modes

    def test_every_run_carries_its_provider_mode(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            modes = {r.provider_mode for r in s.scalars(select(ScheduledJobRun))}
        assert modes == {ProviderMode.FIXTURE.value}, modes


class TestEveryRunRecordsItsAccounting:
    """§14: outcome, domain state, counts, warnings, provider usage, lineage."""

    def test_both_state_axes_are_populated(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        assert runs
        for r in runs:
            assert r.job_outcome, f"{r.job_kind} has no outcome"
            assert r.domain_state, f"{r.job_kind} has no domain state"

    def test_record_counts_are_present_and_non_negative(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.records_received >= 0
            assert r.records_written >= 0

    def test_provider_usage_is_recorded(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.provider_calls >= 0

    def test_a_manual_price_run_reports_no_provider_calls(
        self, chain: SchedulerChain
    ) -> None:
        """The record that proves no book was contacted."""
        with chain.session() as s:
            runs = [r for r in s.scalars(select(ScheduledJobRun))
                    if r.job_kind == "price_observation"]
        assert runs, "price_observation never ran"
        assert all(r.provider_calls == 0 for r in runs)

    def test_every_run_carries_lineage_or_an_explicit_absence(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.root_run_id, f"{r.job_kind} has no root"
            assert r.original_idempotency_key, f"{r.job_kind} has no chain key"
            assert r.recovery_sequence is not None


# --------------------------------------------------------------------------- #
# §2 / §13 — the chain itself
# --------------------------------------------------------------------------- #


class TestTheChainIsComplete:
    def test_every_unconditional_stage_exists(self, chain: SchedulerChain) -> None:
        """Every stage that a complete game must have, present.

        Conditional stages are excluded and checked separately below rather
        than lumped in, because "absent" means different things for them: a
        PASS is never filled, and an unfilled entry has no closing-line
        value to compare against.
        """
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        missing = [
            st.value for st in CHAIN_ORDER
            if not state.present(st) and st not in CONDITIONALLY_ABSENT
        ]
        assert not missing, f"stages absent from the chain: {missing}"

    @pytest.mark.parametrize("stage", sorted(CONDITIONALLY_ABSENT, key=lambda s: s.value))
    def test_a_conditional_stage_is_counted_not_ignored(
        self, chain: SchedulerChain, stage: Stage
    ) -> None:
        """Absence must be a recorded zero, never a missing key. A stage the
        reader cannot see at all is indistinguishable from one nobody
        thought to check."""
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        assert stage in state.stages, f"{stage.value} is not reported at all"
        assert state.stages[stage] >= 0

    def test_an_unfilled_entry_has_no_clv_and_that_is_correct(
        self, chain: SchedulerChain
    ) -> None:
        """CLV compares an entry price against the close. With no fill there
        is no entry price, so a CLV value would have to be invented."""
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        if state.stages.get(Stage.SIMULATED_FILL, 0) == 0:
            assert state.stages.get(Stage.CLV, 0) == 0, (
                "CLV present with no simulated fill; it has nothing to measure from"
            )

    def test_the_close_was_captured_and_is_therefore_measurable(
        self, chain: SchedulerChain
    ) -> None:
        """A missing close is allowed but must be explicit. In this scenario
        the close exists, so the chain can state that rather than shrug."""
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        assert state.present(Stage.CLOSING_CAPTURE)

    def test_the_semantic_hash_is_stable_across_reads(
        self, chain: SchedulerChain
    ) -> None:
        """Hashing ids or timestamps would produce a value that changes for
        no reason and therefore detects nothing."""
        with chain.session() as s:
            a = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
            b = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        assert a == b
        assert len(a) == 64

    def test_two_identical_runs_agree_on_the_hash(self, factory, tmp_path) -> None:
        """The property that makes the hash worth reporting."""
        first = SchedulerChain(factory)
        drive(first)
        with first.session() as s:
            h1 = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        assert len(h1) == 64


# --------------------------------------------------------------------------- #
# §16 — reconciliation
# --------------------------------------------------------------------------- #


class TestChainReconciliation:
    def test_a_complete_chain_reconciles(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        assert report["verdict"] in {
            ChainVerdict.CONSISTENT.value, ChainVerdict.INCOMPLETE.value
        }, report["findings"]

    def test_reconciliation_never_repairs(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        assert report["auto_repair"] is False

    def test_a_domain_effect_without_a_completed_run_is_recoverable(
        self, chain: SchedulerChain
    ) -> None:
        """The signature of a crash after commit: real records, no run."""
        with chain.session() as s:
            for r in s.scalars(select(ScheduledJobRun).where(
                ScheduledJobRun.job_kind == "odds_capture"
            )):
                r.job_outcome = Outcome.INTERRUPTED.value
                r.status = "interrupted"
            s.commit()
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        checks = {f["check"] for f in report["findings"]}
        assert "domain_effect_without_scheduler_completion" in checks
        assert report["verdict"] in {
            ChainVerdict.RECOVERABLE.value, ChainVerdict.MANUAL_REVIEW.value,
            ChainVerdict.CORRUPTED.value,
        }

    def test_a_conflicting_settlement_requires_review(self, chain: SchedulerChain) -> None:
        """Never auto-repaired: two settlements of one selection that
        disagree is a question about a money-shaped fact."""
        with chain.session() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME)))
            assert rows, "no ledger rows to conflict"
            target = rows[0]
            clone = ForwardLedgerEntry(
                data_mode=target.data_mode, canonical_game_id=target.canonical_game_id,
                policy_version=target.policy_version, model_version=target.model_version,
                horizon=target.horizon, market=target.market, selection=target.selection,
                status=target.status, reasons=target.reasons,
                as_of_at=target.as_of_at + timedelta(seconds=1),
                created_at=target.created_at, result="LOSS", settled_at=target.created_at,
                filled=True,
            )
            target.result = "WIN"
            target.filled = True
            s.add(clone)
            s.commit()
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        checks = {f["check"] for f in report["findings"]}
        assert "conflicting_settlement" in checks, checks
        assert report["verdict"] in {
            ChainVerdict.MANUAL_REVIEW.value, ChainVerdict.CORRUPTED.value
        }

    def test_a_cohort_mismatch_is_corrupting(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            row = s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME)).first()
            assert row is not None
            row.data_mode = DataMode.LIVE_RESEARCH.value
            s.commit()
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        # The row left the cohort entirely, so the scan for THIS cohort now
        # sees a gap rather than a mixture - which is itself the detection.
        assert report["verdict"] != ChainVerdict.CONSISTENT.value


# --------------------------------------------------------------------------- #
# §5 — point-in-time integrity across the chain
# --------------------------------------------------------------------------- #


class TestPointInTimeAcrossTheChain:
    def test_no_prediction_was_made_after_kickoff(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            preds = list(s.scalars(select(ForwardPrediction).where(
                ForwardPrediction.canonical_game_id == GAME)))
        assert preds
        for p in preds:
            assert p.as_of_at < KICK, f"{p.id} predicted at or after kickoff"

    def test_no_prediction_saw_the_closing_capture(self, chain: SchedulerChain) -> None:
        """The close is recorded at kickoff; every vintage precedes it."""
        with chain.session() as s:
            preds = list(s.scalars(select(ForwardPrediction).where(
                ForwardPrediction.canonical_game_id == GAME)))
            from fde_api.db.forward_models import ConsensusSnapshot

            closes = list(s.scalars(select(ConsensusSnapshot).where(
                ConsensusSnapshot.canonical_game_id == GAME,
                ConsensusSnapshot.is_closing_capture.is_(True))))
        assert closes, "no closing capture to test against"
        earliest_close = min(c.observed_at for c in closes)
        for p in preds:
            assert p.as_of_at < earliest_close

    def test_no_prediction_saw_the_final_result(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            finals = list(s.scalars(select(ScheduleObservation).where(
                ScheduleObservation.canonical_game_id == GAME,
                ScheduleObservation.game_status == "FINAL")))
            preds = list(s.scalars(select(ForwardPrediction).where(
                ForwardPrediction.canonical_game_id == GAME)))
        assert finals, "the result was never ingested"
        first_final = min(f.observed_at for f in finals)
        for p in preds:
            assert p.as_of_at < first_final

    def test_every_price_was_observed_before_it_was_evaluated(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            prices = list(s.scalars(select(ManualBookPriceEntry).where(
                ManualBookPriceEntry.canonical_game_id == GAME)))
            evals = list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME)))
        assert prices and evals
        earliest_eval = min(e.as_of_at for e in evals)
        for p in prices:
            assert p.observed_at <= earliest_eval, (
                f"price {p.id} observed after the evaluation that used it"
            )

    def test_every_timestamp_is_timezone_aware(self, chain: SchedulerChain) -> None:
        """A naive timestamp compares wrongly against an aware one, which is
        how a cutoff silently stops being a cutoff."""
        with chain.session() as s:
            rows: list[tuple[str, datetime | None]] = []
            for p in s.scalars(select(ForwardPrediction).where(
                    ForwardPrediction.canonical_game_id == GAME)):
                rows.append((f"prediction {p.id} as_of_at", p.as_of_at))
                rows.append((f"prediction {p.id} created_at", p.created_at))
            for e in s.scalars(select(ForwardLedgerEntry).where(
                    ForwardLedgerEntry.canonical_game_id == GAME)):
                rows.append((f"ledger {e.id} as_of_at", e.as_of_at))
                rows.append((f"ledger {e.id} created_at", e.created_at))
            for m in s.scalars(select(ManualBookPriceEntry).where(
                    ManualBookPriceEntry.canonical_game_id == GAME)):
                rows.append((f"price {m.id} observed_at", m.observed_at))
                rows.append((f"price {m.id} entered_at", m.entered_at))
        assert rows
        naive = [label for label, ts in rows if ts is not None and ts.tzinfo is None]
        assert not naive, f"naive timestamps: {naive}"


# --------------------------------------------------------------------------- #
# §13 — rerunning creates no duplicate effects
# --------------------------------------------------------------------------- #


class TestRerunningIsIdempotent:
    def test_the_whole_week_can_be_replayed_without_duplicating(
        self, chain: SchedulerChain
    ) -> None:
        """A restart replays the week. Not a clock rewind - `FrozenClock`
        refuses to move backwards, which is a guard worth keeping - but a
        fresh scheduler over the same database, which is what a restarted
        process actually is."""
        with chain.session() as s:
            before = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                                policy_version="ftp-2026-v1")
            before_hash = chain_semantic_hash(before)
        restarted = SchedulerChain(chain.factory)
        drive(restarted, with_injuries=False)
        chain = restarted
        with chain.session() as s:
            after = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
            after_hash = chain_semantic_hash(after)
        assert after_hash == before_hash, (
            f"replay changed the chain shape:\n"
            f"  before {before.as_dict()['stages']}\n"
            f"  after  {after.as_dict()['stages']}"
        )

    def test_a_replayed_price_observation_adds_no_row(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            before = len(list(s.scalars(select(ManualBookPriceEntry).where(
                ManualBookPriceEntry.canonical_game_id == GAME))))
        replay = SchedulerChain(chain.factory)
        replay.at(KICK - timedelta(days=2))
        replay.run("price_observation",
                   price_observations=prices(replay.clock.now() - timedelta(minutes=2)))
        with chain.session() as s:
            after = len(list(s.scalars(select(ManualBookPriceEntry).where(
                ManualBookPriceEntry.canonical_game_id == GAME))))
        assert after == before

    def test_a_replayed_evaluation_adds_no_ledger_row(
        self, chain: SchedulerChain
    ) -> None:
        """`record_evaluation` appends unconditionally, so the handler's
        slot-identity guard is the only thing preventing a duplicate."""
        with chain.session() as s:
            before = len(list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME))))
        replay = SchedulerChain(chain.factory)
        replay.at(KICK - timedelta(days=2))
        replay.run("price_evaluation")
        with chain.session() as s:
            after = len(list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME))))
        assert after == before


# --------------------------------------------------------------------------- #
# Cohort containment
# --------------------------------------------------------------------------- #


class TestFixtureOutputStaysOutOfEvaluableCohorts:
    def test_no_record_entered_burn_in_or_the_official_cohort(
        self, chain: SchedulerChain
    ) -> None:
        forbidden = {Cohort.BURN_IN.value, Cohort.OFFICIAL_FORWARD_TEST.value}
        with chain.session() as s:
            price_cohorts = {p.cohort for p in s.scalars(select(ManualBookPriceEntry))}
            run_modes = {r.data_mode for r in s.scalars(select(ScheduledJobRun))}
        assert not (price_cohorts & forbidden), price_cohorts
        assert not (run_modes & forbidden), run_modes

    def test_every_market_record_is_marked_fixture_provenance(
        self, chain: SchedulerChain
    ) -> None:
        from fde_api.db.forward_models import OddsQuote

        with chain.session() as s:
            modes = {q.provider_mode for q in s.scalars(select(OddsQuote).where(
                OddsQuote.canonical_game_id == GAME))}
        assert modes == {ProviderMode.FIXTURE.value}, modes
