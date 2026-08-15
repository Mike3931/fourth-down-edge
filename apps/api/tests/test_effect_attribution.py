"""Prior effects must be attributed to the run, not counted table-wide.

`inspect_prior_effects` decides whether a crashed run already committed its
domain writes. It answered that by counting rows in the job's table -
`SELECT count(*) FROM the_whole_table` - for every job except
`odds_capture`. So any pre-existing row counted as this run's effects: a
row from a different slot, a different game, or a different cohort.

For OBSERVATION and SNAPSHOT jobs the decision is "replay anyway", so the
consequence was a wrong reason on a right action. For TERMINAL jobs it
changed behaviour: one settled ledger entry anywhere in the database made
every settlement recovery conclude PRIOR_EFFECTS_NO_REPLAY, so the
recovery finalised without settling and the settlement silently never
happened.

Each test below constructs a row that the census would have counted and
the attribution rule must not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import (
    ConsensusSnapshot,
    ForwardLedgerEntry,
    OddsQuote,
    WeatherForecastVintage,
)
from fde_api.db.models import Base
from fde_api.forward.recovery import (
    Attribution,
    ReplayDecision,
    inspect_prior_effects,
)

SLOT = datetime(2026, 9, 10, 17, 0, tzinfo=UTC)
OTHER_SLOT = SLOT + timedelta(hours=6)
KEY = "odds_capture:burn_in:20260910T170000Z"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _weather(db: Session, *, observed_at: datetime, game: str = "g1",
             data_mode: str = "burn_in") -> None:
    db.add(WeatherForecastVintage(
        data_mode=data_mode, canonical_game_id=game, observed_at=observed_at,
        raw_hash=f"h{observed_at.isoformat()}{game}{data_mode}",
    ))
    db.commit()


def _consensus(db: Session, *, observed_at: datetime, game: str = "g1",
               data_mode: str = "burn_in") -> None:
    db.add(ConsensusSnapshot(
        data_mode=data_mode, cohort="burn_in", min_books_applied=3, canonical_game_id=game, market="SPREAD",
        method_version="v1", provider_mode="FIXTURE", eligible_books=0,
        quote_ids=[], observed_at=observed_at,
    ))
    db.commit()


def _ledger(db: Session, *, game: str, data_mode: str = "burn_in") -> None:
    db.add(ForwardLedgerEntry(
        data_mode=data_mode, cohort="burn_in", canonical_game_id=game, policy_version="ftp-2026-v1",
        model_version="m1", horizon="T-24h", market="SPREAD", status="RESEARCH_CANDIDATE",
        reasons=[], as_of_at=SLOT, created_at=SLOT, result="WIN", settled_at=SLOT,
    ))
    db.commit()


def _quote(db: Session, *, request_id: str | None, game: str = "g1") -> None:
    db.add(OddsQuote(
        data_mode="burn_in", canonical_game_id=game, provider="fixture",
        provider_mode="FIXTURE", sportsbook="book", market="SPREAD", selection="HOME",
        american=-110, decimal_odds=1.909, observed_at=SLOT,
        raw_hash=f"q{request_id}{game}", request_id=request_id,
    ))
    db.commit()


class TestTerminalJobsRefuseToGuess:
    def test_settlement_without_a_scope_demands_review(self, db: Session) -> None:
        """The defect, stated directly. A settled entry for a DIFFERENT game
        used to make this recovery skip settlement entirely."""
        _ledger(db, game="some-other-game")
        insp = inspect_prior_effects(
            db, job_kind="settlement", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in",
        )
        assert insp.recommended_decision is ReplayDecision.MANUAL_REVIEW_REQUIRED
        assert insp.attribution is Attribution.SCOPE_REQUIRED
        assert "canonical_game_id" in insp.reason

    def test_a_scoped_settlement_sees_only_its_own_game(self, db: Session) -> None:
        _ledger(db, game="other-game")
        insp = inspect_prior_effects(
            db, job_kind="settlement", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", canonical_game_id="the-game",
        )
        assert insp.actual_effect_count == 0
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY

    def test_a_scoped_settlement_finds_its_own_effect(self, db: Session) -> None:
        _ledger(db, game="the-game")
        insp = inspect_prior_effects(
            db, job_kind="settlement", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", canonical_game_id="the-game",
        )
        assert insp.actual_effect_count == 1
        assert insp.recommended_decision is ReplayDecision.PRIOR_EFFECTS_NO_REPLAY

    def test_a_settlement_in_another_cohort_is_not_this_run_s(self, db: Session) -> None:
        """Cohorts are separate universes; a demo settlement is not evidence
        about a burn-in one."""
        _ledger(db, game="the-game", data_mode="demo")
        insp = inspect_prior_effects(
            db, job_kind="settlement", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", canonical_game_id="the-game",
        )
        assert insp.actual_effect_count == 0


class TestSnapshotJobsAttributeByExactSlot:
    def test_a_snapshot_at_another_slot_is_not_counted(self, db: Session) -> None:
        _consensus(db, observed_at=OTHER_SLOT)
        insp = inspect_prior_effects(
            db, job_kind="consensus_build", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in",
        )
        assert insp.attribution is Attribution.EXACT_SLOT
        assert insp.actual_effect_count == 0
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY

    def test_a_snapshot_at_this_slot_is_counted(self, db: Session) -> None:
        _consensus(db, observed_at=SLOT)
        insp = inspect_prior_effects(
            db, job_kind="consensus_build", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in",
        )
        assert insp.actual_effect_count == 1
        assert insp.recommended_decision is ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY

    def test_the_scope_is_recorded_for_audit(self, db: Session) -> None:
        """A count means nothing without the rule that produced it."""
        _consensus(db, observed_at=SLOT)
        insp = inspect_prior_effects(
            db, job_kind="consensus_build", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in",
        )
        assert SLOT.isoformat() in insp.attribution_detail
        assert "data_mode == burn_in" in insp.attribution_detail
        assert insp.as_dict()["attribution"] == "EXACT_SLOT"


class TestObservationJobsAttributeByWindow:
    def test_a_capture_inside_the_window_is_counted(self, db: Session) -> None:
        _weather(db, observed_at=SLOT + timedelta(minutes=20))
        insp = inspect_prior_effects(
            db, job_kind="weather_capture", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", attribution_window=timedelta(hours=1),
        )
        assert insp.attribution is Attribution.SLOT_WINDOW
        assert insp.actual_effect_count == 1

    def test_a_capture_after_the_window_belongs_to_the_next_slot(self, db: Session) -> None:
        """The window must not be wide enough to swallow the successor."""
        _weather(db, observed_at=SLOT + timedelta(hours=2))
        insp = inspect_prior_effects(
            db, job_kind="weather_capture", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", attribution_window=timedelta(hours=1),
        )
        assert insp.actual_effect_count == 0

    def test_a_capture_before_the_slot_is_not_this_run_s(self, db: Session) -> None:
        _weather(db, observed_at=SLOT - timedelta(minutes=1))
        insp = inspect_prior_effects(
            db, job_kind="weather_capture", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", attribution_window=timedelta(hours=1),
        )
        assert insp.actual_effect_count == 0

    def test_the_window_is_half_open_at_its_end(self, db: Session) -> None:
        """Exactly at slot+window belongs to the NEXT slot, or two runs would
        both claim the same row."""
        _weather(db, observed_at=SLOT + timedelta(hours=1))
        insp = inspect_prior_effects(
            db, job_kind="weather_capture", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in", attribution_window=timedelta(hours=1),
        )
        assert insp.actual_effect_count == 0


class TestRequestIdAttribution:
    def test_a_quote_from_this_chain_is_counted(self, db: Session) -> None:
        _quote(db, request_id=KEY)
        insp = inspect_prior_effects(
            db, job_kind="odds_capture", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in",
        )
        assert insp.attribution is Attribution.REQUEST_ID
        assert insp.actual_effect_count == 1

    def test_a_quote_from_another_request_is_not(self, db: Session) -> None:
        _quote(db, request_id="a-different-run", game="g2")
        insp = inspect_prior_effects(
            db, job_kind="odds_capture", idempotency_key=KEY, logical_slot=SLOT,
            data_mode="burn_in",
        )
        assert insp.actual_effect_count == 0


class TestUnattributableRuns:
    def test_no_slot_means_the_conclusion_is_stated_as_such(self, db: Session) -> None:
        """Absence of evidence, reported as absence of evidence."""
        _consensus(db, observed_at=SLOT)
        insp = inspect_prior_effects(
            db, job_kind="consensus_build", idempotency_key=KEY, logical_slot=None,
            data_mode="burn_in",
        )
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
        assert "could not be attributed" in insp.reason
        assert insp.attribution_detail == "unattributable: no logical slot"

    def test_an_untracked_job_is_safe_to_replay(self, db: Session) -> None:
        insp = inspect_prior_effects(
            db, job_kind="closing_capture", idempotency_key=KEY, logical_slot=SLOT,
        )
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
        assert insp.record_type == "unknown"
