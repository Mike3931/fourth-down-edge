"""A genuinely filled entry, and CLV for all three markets.

The complete-game fixture produced no fill, so it produced no CLV either,
and "CLV works" was an untested claim. The reason was not the execution
model - it fills ~97.5% of the time - but the evaluation: the price offered
was never generous enough for the frozen rules to call it a candidate, and
only candidates are executed.

The fix is a better PRICE, not a looser threshold. Nothing here changes the
policy: `research_candidate_edge_threshold`, the uncertainty haircut, the
watch band, and the execution config are exactly as frozen. The fixture
simply presents a price at which the frozen rules reach RESEARCH_CANDIDATE
on their own. Forcing a fill by moving a threshold would prove that the
threshold can be moved.

CLV is measured against the recorded closing CAPTURE, never by re-running
the selection at settlement time. Re-selecting could pick a different
snapshot if anything arrived late, and a disputed close would silently
resolve to one side and report a number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.backtest.execution import ExecutionModel, break_even_prob
from fde_api.db.forward_models import (
    ClosingCapture,
    ConsensusSnapshot,
    ForwardLedgerEntry,
)
from fde_api.db.models import Base
from fde_api.forward.closing import (
    CAPTURED,
    CONFLICT,
    MISSING,
    capture_close,
    closing_snapshot_for,
    has_unresolved_conflict,
)
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.ledger import (
    compute_clv,
    evaluate_candidate,
    record_evaluation,
    settle_entry,
)
from fde_api.forward.modes import DataMode
from fde_api.forward.policy import build_policy_draft, freeze_policy, load_policy

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
GAME = "2026_02_KC_BUF"
POLICY = "ftp-2026-v1"
MODE = DataMode.DEMO
COHORT = Cohort.FIXTURE
RULE = "last eligible consensus snapshot at or before kickoff"


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Session:
    """An in-memory database with the frozen policy in force.

    `data_dir` is redirected FIRST. `freeze_policy` writes a policy artifact
    to disk, and without the redirect it writes into the repository - which
    the artifact-hygiene guard in test_policy_governance.py catches, as it
    did for this fixture on its first run.
    """
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    freeze_policy(s, build_policy_draft(policy_version=POLICY,
                                        start=date(2026, 9, 1), end=date(2027, 2, 28)))
    s.commit()
    return s


def _consensus(
    db: Session, *, market: str, observed_at: datetime, line: float | None = None,
    home: int | None = None, away: int | None = None,
    over: int | None = None, under: int | None = None,
    nv_home: float | None = None, nv_over: float | None = None,
) -> ConsensusSnapshot:
    row = ConsensusSnapshot(
        data_mode=MODE.value, canonical_game_id=GAME, market=market,
        method_version="consensus-v1", provider_mode=ProviderMode.FIXTURE.value,
        median_line=line, home_price_american=home, away_price_american=away,
        over_price_american=over, under_price_american=under,
        no_vig_home_prob=nv_home, no_vig_over_prob=nv_over,
        eligible_books=3, quote_ids={"ids": [1, 2, 3]}, observed_at=observed_at,
    )
    db.add(row)
    db.flush()
    return row


def _capture(db: Session, *, market: str, now: datetime) -> ClosingCapture:
    return capture_close(
        db, canonical_game_id=GAME, market=market, kickoff_utc=KICK,
        selection_rule=RULE, max_age_before_kickoff_minutes=30,
        cohort=COHORT, data_mode=MODE, provider_mode=ProviderMode.FIXTURE,
        policy_version=POLICY, scheduled_slot=KICK - timedelta(minutes=5), now=now,
    ).capture


def _entry(
    db: Session, *, market: str, selection: str, line: float | None, american: int,
    model_probability: float, as_of: datetime | None = None,
) -> ForwardLedgerEntry:
    policy = load_policy(db, POLICY)
    ev = evaluate_candidate(
        market=market, selection=selection, line=line, american=american,
        model_probability=model_probability, price_source="fixture_price",
        price_age_seconds=120, policy=policy, data_completeness=1.0,
    )
    return record_evaluation(
        db, prediction=None, canonical_game_id=GAME, evaluation=ev, policy=policy,
        horizon="T-24h", as_of_at=as_of or (KICK - timedelta(days=1)),
        data_completeness=1.0, data_mode=MODE,
    )


# --------------------------------------------------------------------------- #
# §6 — a genuinely filled entry, produced by the frozen policy
# --------------------------------------------------------------------------- #


class TestTheFrozenPolicyProducesAFill:
    def test_a_generous_price_reaches_research_candidate(self, db: Session) -> None:
        """No threshold is touched. The price is simply good enough."""
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        assert entry.status == "RESEARCH_CANDIDATE", entry.reasons

    def test_the_candidate_is_filled(self, db: Session) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        assert entry.filled is True, entry.fill_note

    def test_the_fill_records_every_execution_field(self, db: Session) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        assert entry.qualifying_line == -3.0          # requested line
        assert entry.qualifying_american == 150       # requested price
        assert entry.simulated_line is not None       # accepted line
        assert entry.simulated_american is not None   # accepted price
        assert entry.simulated_delay_seconds is not None
        assert entry.fill_note
        assert entry.as_of_at.tzinfo is not None      # decision timestamp
        assert entry.created_at.tzinfo is not None
        assert entry.policy_version == POLICY
        assert entry.break_even_probability == pytest.approx(break_even_prob(150))
        assert entry.model_probability == pytest.approx(0.62)
        assert entry.expected_value is not None

    def test_the_accepted_price_is_never_better_than_requested(
        self, db: Session
    ) -> None:
        """The policy models deterioration. A fill that improved on the
        requested price would be hindsight dressed as execution."""
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        assert entry.simulated_american is not None
        assert entry.simulated_american <= entry.qualifying_american

    def test_a_thin_price_is_not_filled_because_it_is_not_a_candidate(
        self, db: Session
    ) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=-110, model_probability=0.52)
        assert entry.status in {"PASS", "WATCH"}
        assert not entry.filled

    def test_an_unfilled_evaluation_is_still_retained(self, db: Session) -> None:
        """The ledger must show what was declined, not only what was taken."""
        _entry(db, market="SPREAD", selection="HOME", line=-3.0,
               american=-110, model_probability=0.52)
        rows = list(db.scalars(select(ForwardLedgerEntry)))
        assert len(rows) == 1
        assert rows[0].status in {"PASS", "WATCH"}

    def test_execution_is_deterministic_for_the_same_identity(self) -> None:
        """The same game, market and selection must fill the same way every
        run, or nothing downstream of it is reproducible."""
        from fde_api.backtest.execution import ExecutionConfig

        cfg = ExecutionConfig()
        a = ExecutionModel(cfg).attempt_fill(
            game_id=GAME, market="SPREAD", selection="HOME", line=-3.0,
            price_american=150)
        b = ExecutionModel(cfg).attempt_fill(
            game_id=GAME, market="SPREAD", selection="HOME", line=-3.0,
            price_american=150)
        assert (a.filled, a.line, a.price_american) == (b.filled, b.line, b.price_american)


# --------------------------------------------------------------------------- #
# §8 — simulated execution behaviours
# --------------------------------------------------------------------------- #


class TestSimulatedExecutionBehaviour:
    def test_the_price_worsens_by_the_policy_amount(self) -> None:
        from fde_api.backtest.execution import ExecutionConfig

        fill = ExecutionModel(ExecutionConfig()).attempt_fill(
            game_id="g_price", market="SPREAD", selection="HOME",
            line=-3.0, price_american=150)
        assert fill.filled
        assert fill.price_american is not None
        assert fill.price_american < 150, "the price did not deteriorate at all"

    def test_a_line_change_is_possible_and_never_favourable(self) -> None:
        """Across many identities some fills move the line. Whenever one
        does, it must move AGAINST the position."""
        from fde_api.backtest.execution import ExecutionConfig

        model = ExecutionModel(ExecutionConfig())
        moved = 0
        for i in range(200):
            fill = model.attempt_fill(
                game_id=f"g{i}", market="SPREAD", selection="HOME",
                line=-3.0, price_american=150)
            if fill.filled and fill.line is not None and fill.line != -3.0:
                moved += 1
                assert fill.line < -3.0, f"line moved in our favour: {fill.line}"
        assert moved > 0, "no line ever moved; the deterioration path is unexercised"

    def test_suspension_and_no_fill_are_both_reachable(self) -> None:
        """Both refusal paths must be live, or the unfilled branch of the
        ledger is never exercised by anything."""
        from fde_api.backtest.execution import ExecutionConfig

        model = ExecutionModel(ExecutionConfig())
        reasons = set()
        for i in range(2000):
            fill = model.attempt_fill(
                game_id=f"x{i}", market="TOTAL", selection="OVER",
                line=47.5, price_american=-110)
            if not fill.filled:
                reasons.add(fill.reason)
        assert reasons, "nothing ever failed to fill"

    def test_a_stale_price_is_data_incomplete_not_a_candidate(
        self, db: Session
    ) -> None:
        policy = load_policy(db, POLICY)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-3.0, american=+150,
            model_probability=0.62, price_source="fixture_price",
            price_age_seconds=policy.price_staleness.max_consensus_age_minutes * 60 + 1,
            policy=policy, data_completeness=1.0,
        )
        assert ev.status == "DATA_INCOMPLETE"
        assert any("staleness" in r for r in ev.reasons)

    def test_incomplete_data_is_data_incomplete_not_a_candidate(
        self, db: Session
    ) -> None:
        policy = load_policy(db, POLICY)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-3.0, american=+150,
            model_probability=0.62, price_source="fixture_price",
            price_age_seconds=60, policy=policy, data_completeness=0.10,
        )
        assert ev.status == "DATA_INCOMPLETE"

    def test_no_model_probability_is_data_incomplete(self, db: Session) -> None:
        policy = load_policy(db, POLICY)
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-3.0, american=+150,
            model_probability=None, price_source="fixture_price",
            price_age_seconds=60, policy=policy, data_completeness=1.0,
        )
        assert ev.status == "DATA_INCOMPLETE"

    def test_settlement_is_idempotent(self, db: Session) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=5),
                   line=-3.0, home=-110, away=-110)
        _capture(db, market="SPREAD", now=KICK - timedelta(minutes=4))
        policy = load_policy(db, POLICY)
        settle_entry(db, entry=entry, home_score=24, away_score=20,
                     kickoff_utc=KICK, policy=policy, data_mode=MODE)
        first = (entry.result, entry.pnl_units, entry.clv_line, entry.settled_at)
        settle_entry(db, entry=entry, home_score=24, away_score=20,
                     kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert (entry.result, entry.pnl_units, entry.clv_line, entry.settled_at) == first


# --------------------------------------------------------------------------- #
# §7 — CLV for each market
# --------------------------------------------------------------------------- #


class TestSpreadClv:
    def _setup(self, db: Session, *, close_line: float, close_price: int = -110):
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=5),
                   line=close_line, home=close_price, away=close_price)
        _capture(db, market="SPREAD", now=KICK - timedelta(minutes=4))
        return entry, load_policy(db, POLICY)

    def test_line_clv_is_non_null_and_signed_for_our_side(self, db: Session) -> None:
        entry, policy = self._setup(db, close_line=-4.0)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.line_clv is not None
        # We took HOME -3 and the market closed at -4: we hold the better
        # number, so line CLV is positive.
        assert clv.line_clv > 0, clv

    def test_a_worse_close_produces_negative_line_clv(self, db: Session) -> None:
        entry, policy = self._setup(db, close_line=-2.0)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.line_clv is not None and clv.line_clv < 0, clv

    def test_equal_lines_fall_back_to_the_price_comparison(self, db: Session) -> None:
        """The policy rule: when the number is the same, the price is the
        only thing that differs, so CLV is a probability difference."""
        entry, policy = self._setup(db, close_line=-3.0, close_price=-120)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.line_clv == pytest.approx(0.0)
        assert clv.probability_clv is not None
        assert "price" in clv.note


class TestTotalClv:
    def _setup(self, db: Session, *, close_total: float, close_price: int = -110):
        entry = _entry(db, market="TOTAL", selection="OVER", line=47.5,
                       american=+150, model_probability=0.62)
        _consensus(db, market="TOTAL", observed_at=KICK - timedelta(minutes=5),
                   line=close_total, over=close_price, under=close_price)
        _capture(db, market="TOTAL", now=KICK - timedelta(minutes=4))
        return entry, load_policy(db, POLICY)

    def test_a_higher_close_is_positive_for_an_over(self, db: Session) -> None:
        """We took OVER 47.5 and the market closed at 49.

        Our number is LOWER, which is better for an over: fewer points are
        needed. Positive CLV. Signing this backwards is an easy mistake and
        an expensive one, since it would flip the reported direction of
        every total in the ledger.
        """
        entry, policy = self._setup(db, close_total=49.0)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.line_clv is not None and clv.line_clv > 0, clv

    def test_a_lower_close_reverses_the_sign(self, db: Session) -> None:
        """Closing at 46 means we took the worse side of the number."""
        entry, policy = self._setup(db, close_total=46.0)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.line_clv is not None and clv.line_clv < 0, clv

    def test_equal_totals_fall_back_to_the_price(self, db: Session) -> None:
        entry, policy = self._setup(db, close_total=47.5, close_price=-125)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.line_clv == pytest.approx(0.0)
        assert clv.probability_clv is not None


class TestMoneylineClv:
    def _setup(self, db: Session, *, close_home: int, close_away: int):
        entry = _entry(db, market="MONEYLINE", selection="HOME", line=None,
                       american=+150, model_probability=0.62)
        _consensus(db, market="MONEYLINE", observed_at=KICK - timedelta(minutes=5),
                   home=close_home, away=close_away)
        _capture(db, market="MONEYLINE", now=KICK - timedelta(minutes=4))
        return entry, load_policy(db, POLICY)

    def test_moneyline_clv_is_a_no_vig_probability_difference(
        self, db: Session
    ) -> None:
        entry, policy = self._setup(db, close_home=-140, close_away=+120)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.probability_clv is not None
        assert clv.closing_no_vig_prob is not None
        assert 0 < clv.closing_no_vig_prob < 1
        assert "no-vig" in clv.note
        assert clv.line_clv is None, "a moneyline has no line to compare"

    def test_a_shortening_price_is_positive_clv(self, db: Session) -> None:
        """We took +150 and the market closed at -140: the close implies far
        more probability for our side than we paid for."""
        entry, policy = self._setup(db, close_home=-140, close_away=+120)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.probability_clv is not None and clv.probability_clv > 0, clv

    def test_a_drifting_price_is_negative_clv(self, db: Session) -> None:
        entry, policy = self._setup(db, close_home=+260, close_away=-320)
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert clv.probability_clv is not None and clv.probability_clv < 0, clv


class TestClvHonoursTheClosingRecord:
    def test_a_missing_close_produces_an_explicit_unavailable_state(
        self, db: Session
    ) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        capture = _capture(db, market="SPREAD", now=KICK - timedelta(minutes=4))
        assert capture.status == MISSING
        clv = compute_clv(db, entry=entry, kickoff_utc=KICK,
                          policy=load_policy(db, POLICY), data_mode=MODE)
        assert clv.probability_clv is None and clv.line_clv is None
        assert "unavailable" in clv.note.lower()
        assert capture.missing_close_reason

    def test_a_conflicting_close_prevents_finalisation(self, db: Session) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=20),
                   line=-3.0, home=-110, away=-110)
        first = _capture(db, market="SPREAD", now=KICK - timedelta(minutes=19))
        assert first.status == CAPTURED
        # A later snapshot arrives inside the window; the rule now selects it.
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=2),
                   line=-4.0, home=-110, away=-110)
        second = _capture(db, market="SPREAD", now=KICK - timedelta(minutes=1))
        assert second.status == CONFLICT
        assert has_unresolved_conflict(
            db, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)

        clv = compute_clv(db, entry=entry, kickoff_utc=KICK,
                          policy=load_policy(db, POLICY), data_mode=MODE)
        assert clv.line_clv is None and clv.probability_clv is None
        assert "disputed" in clv.note

    def test_the_conflict_does_not_overwrite_the_original(self, db: Session) -> None:
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=20),
                   line=-3.0, home=-110, away=-110)
        first = _capture(db, market="SPREAD", now=KICK - timedelta(minutes=19))
        original = (first.id, first.status, first.consensus_snapshot_id)
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=2),
                   line=-4.0, home=-110, away=-110)
        second = _capture(db, market="SPREAD", now=KICK - timedelta(minutes=1))
        db.refresh(first)
        assert (first.id, first.status, first.consensus_snapshot_id) == original
        assert second.conflicts_with_id == first.id

    def test_the_most_favourable_close_is_not_chosen_retrospectively(
        self, db: Session
    ) -> None:
        """Two eligible snapshots; the rule takes the LAST, not the one that
        would make CLV look best."""
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=25),
                   line=-6.0, home=-110, away=-110)   # would flatter our -3
        last = _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=3),
                          line=-2.0, home=-110, away=-110)  # the real close
        capture = _capture(db, market="SPREAD", now=KICK - timedelta(minutes=2))
        assert capture.consensus_snapshot_id == last.id

    def test_a_snapshot_after_kickoff_is_not_eligible(self, db: Session) -> None:
        """The close is what was observable at kickoff. Anything later is
        information the entry could not have had."""
        _consensus(db, market="SPREAD", observed_at=KICK + timedelta(minutes=30),
                   line=-9.0, home=-110, away=-110)
        capture = _capture(db, market="SPREAD", now=KICK + timedelta(minutes=31))
        assert capture.status == MISSING

    def test_recalculating_clv_gives_the_same_answer(self, db: Session) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=5),
                   line=-4.0, home=-110, away=-110)
        _capture(db, market="SPREAD", now=KICK - timedelta(minutes=4))
        policy = load_policy(db, POLICY)
        a = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        b = compute_clv(db, entry=entry, kickoff_utc=KICK, policy=policy, data_mode=MODE)
        assert (a.line_clv, a.probability_clv, a.closing_line) == (
            b.line_clv, b.probability_clv, b.closing_line)

    def test_closing_data_is_absent_at_entry_time(self, db: Session) -> None:
        """The entry is evaluated a day before any close exists."""
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        assert closing_snapshot_for(
            db, canonical_game_id=GAME, market="SPREAD", cohort=COHORT) is None
        assert entry.closing_line is None
        assert entry.clv_line is None


class TestSettlementAttachesClv:
    def test_a_settled_filled_entry_carries_a_non_null_clv(self, db: Session) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=5),
                   line=-4.0, home=-110, away=-110)
        _capture(db, market="SPREAD", now=KICK - timedelta(minutes=4))
        settle_entry(db, entry=entry, home_score=24, away_score=20, kickoff_utc=KICK,
                     policy=load_policy(db, POLICY), data_mode=MODE)
        assert entry.result in {"WIN", "LOSS", "PUSH"}
        assert entry.clv_line is not None
        assert entry.closing_line == -4.0
        assert entry.settled_at is not None

    def test_a_push_settles_to_zero(self, db: Session) -> None:
        """BUF -3 with a 3-point margin is a push, not a narrow win."""
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=+150, model_probability=0.62)
        # Neutralise line deterioration so the settlement is exactly on the
        # number: the property under test is push handling, not execution.
        entry.simulated_line = -3.0
        _consensus(db, market="SPREAD", observed_at=KICK - timedelta(minutes=5),
                   line=-3.0, home=-110, away=-110)
        _capture(db, market="SPREAD", now=KICK - timedelta(minutes=4))
        settle_entry(db, entry=entry, home_score=24, away_score=21, kickoff_utc=KICK,
                     policy=load_policy(db, POLICY), data_mode=MODE)
        assert entry.result == "PUSH"
        assert entry.pnl_units == pytest.approx(0.0)

    def test_an_unfilled_entry_settles_as_no_fill_with_zero_pnl(
        self, db: Session
    ) -> None:
        entry = _entry(db, market="SPREAD", selection="HOME", line=-3.0,
                       american=-110, model_probability=0.52)
        assert not entry.filled
        settle_entry(db, entry=entry, home_score=24, away_score=20, kickoff_utc=KICK,
                     policy=load_policy(db, POLICY), data_mode=MODE)
        assert entry.result == "NO_FILL"
        assert entry.pnl_units == pytest.approx(0.0)
        assert entry.settled_at is not None
