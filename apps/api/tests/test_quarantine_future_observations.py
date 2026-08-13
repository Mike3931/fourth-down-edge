"""Fixture rows with an impossible instant, moved out of the live cohort.

27 records carried an `observed_at` a month ahead of the clock that wrote
them — 24 quotes and 3 consensus snapshots, all stamped 2026-09-10 22:55
for `2026_01_SF_LA`, all written during a fixture run on 2026-08-01. They
are invisible to every point-in-time read until wall time passes them, and
then become the freshest prices available for a game kicking off 100
minutes later.

Re-stamping them would be wrong. Their `provider_timestamp` carries the
same future instant, so the fixture itself described that moment and the
capture recorded it faithfully. Nothing was mis-transcribed. What is wrong
is that fixture output is sitting in LIVE_RESEARCH, which the project's
standing rule forbids outright, and `provider_mode` already says
UNKNOWN_LEGACY.

So the cohort is corrected and the rows are kept in full. `data_mode` is
the isolation every live-research query already applies.

The consensus rows need more than an UPDATE: `cohort` is one of the
CONSENSUS logical identity fields, so a snapshot that changes cohort
changes identity, and a stored `logical_identity_hash` computed under the
old one would be a hash of fields the row no longer has.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote
from fde_api.db.models import Base
from fde_api.forward.consensus import CONSENSUS_METHOD_VERSION
from fde_api.forward.domain_identity import CONSENSUS

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
FUTURE = datetime(2026, 9, 10, 22, 55, tzinfo=UTC)
GAME = "2026_01_SF_LA"


def _module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "quarantine_future_observations.py"
    spec = importlib.util.spec_from_file_location("_quarantine", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_quarantine"] = mod
    spec.loader.exec_module(mod)
    return mod


ops = _module()


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


def _quote(db: Session, *, observed_at: datetime, mode: str = "LIVE_RESEARCH",
           market: str = "SPREAD", selection: str = "HOME") -> OddsQuote:
    q = OddsQuote(
        data_mode=mode, canonical_game_id=GAME, provider="the-odds-api",
        provider_mode="UNKNOWN_LEGACY", sportsbook="draftkings", market=market,
        selection=selection, line=-2.5, american=-110, decimal_odds=1.909,
        is_live=False, observed_at=observed_at,
        raw_hash=f"h{observed_at.isoformat()}{market}{selection}{mode}",
    )
    db.add(q)
    db.flush()
    return q


def _snapshot(db: Session, *, observed_at: datetime, mode: str = "LIVE_RESEARCH",
              market: str = "SPREAD") -> ConsensusSnapshot:
    c = ConsensusSnapshot(
        data_mode=mode, canonical_game_id=GAME, market=market,
        method_version=CONSENSUS_METHOD_VERSION, provider_mode="UNKNOWN_LEGACY",
        median_line=-2.5, eligible_books=3,
        quote_ids={"quote_ids": [], "books": []}, observed_at=observed_at,
        logical_identity_hash=CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": market, "cutoff": observed_at,
            "cohort": mode, "method_version": CONSENSUS_METHOD_VERSION,
        }),
    )
    db.add(c)
    db.flush()
    return c


class TestOnlyTheImpossibleRowsMove:
    def test_a_future_quote_is_selected(self, db: Session) -> None:
        _quote(db, observed_at=FUTURE)
        changes, _ = ops.plan(db, now=NOW)
        assert [c["table"] for c in changes] == ["odds_quotes"]

    def test_a_past_quote_is_left_alone(self, db: Session) -> None:
        _quote(db, observed_at=NOW - timedelta(days=1))
        assert ops.plan(db, now=NOW)[0] == []

    def test_a_quote_at_the_present_instant_is_left_alone(self, db: Session) -> None:
        """The boundary. `observed_at == now` is a record of this moment,
        not of a moment that has not happened."""
        _quote(db, observed_at=NOW)
        assert ops.plan(db, now=NOW)[0] == []

    def test_a_row_already_in_demo_is_not_moved_again(self, db: Session) -> None:
        """Idempotent: running it twice must be free."""
        _quote(db, observed_at=FUTURE, mode="DEMO")
        assert ops.plan(db, now=NOW)[0] == []

    def test_both_tables_are_covered(self, db: Session) -> None:
        _quote(db, observed_at=FUTURE)
        _snapshot(db, observed_at=FUTURE)
        changes, _ = ops.plan(db, now=NOW)
        assert {c["table"] for c in changes} == {"odds_quotes", "consensus_snapshots"}


class TestTheConsensusIdentityIsRecomputed:
    def test_the_new_hash_is_the_one_the_demo_cohort_implies(
        self, db: Session
    ) -> None:
        """Not merely different — equal to what `build_consensus` would
        have stored had it written the row into DEMO in the first place."""
        _snapshot(db, observed_at=FUTURE)
        changes, _ = ops.plan(db, now=NOW)
        snap = next(c for c in changes if c["table"] == "consensus_snapshots")
        assert snap["new_logical_hash"] == CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": FUTURE,
            "cohort": "DEMO", "method_version": CONSENSUS_METHOD_VERSION,
        })

    def test_it_actually_differs_from_the_live_one(self, db: Session) -> None:
        """If cohort were not in the logical identity this whole step would
        be unnecessary, so the test says which of those worlds we are in."""
        row = _snapshot(db, observed_at=FUTURE)
        changes, _ = ops.plan(db, now=NOW)
        snap = next(c for c in changes if c["table"] == "consensus_snapshots")
        assert snap["new_logical_hash"] != row.logical_identity_hash

    def test_a_quote_needs_no_hash_because_it_carries_none(
        self, db: Session
    ) -> None:
        _quote(db, observed_at=FUTURE)
        changes, _ = ops.plan(db, now=NOW)
        assert changes[0]["new_logical_hash"] is None


class TestItRefusesToCreateACollision:
    def test_a_slot_already_occupied_in_demo_is_refused(self, db: Session) -> None:
        """Two records at one identity is the condition `domain_identity`
        exists to detect. Creating one while tidying another would be
        absurd, so the row stays where it is and is reported."""
        _snapshot(db, observed_at=FUTURE, mode="DEMO")
        _snapshot(db, observed_at=FUTURE, mode="LIVE_RESEARCH")
        changes, refusals = ops.plan(db, now=NOW)
        assert changes == []
        assert any("already occupies this slot" in r for r in refusals)

    def test_a_different_market_is_not_a_collision(self, db: Session) -> None:
        _snapshot(db, observed_at=FUTURE, mode="DEMO", market="TOTAL")
        _snapshot(db, observed_at=FUTURE, mode="LIVE_RESEARCH", market="SPREAD")
        changes, refusals = ops.plan(db, now=NOW)
        assert len(changes) == 1 and refusals == []


class TestTheLiveCohortStopsSeeingThem:
    def test_the_quotes_are_gone_from_live_research_after_the_move(
        self, db: Session
    ) -> None:
        """The point of the exercise, asserted through the filter every
        live-research query already applies."""
        q = _quote(db, observed_at=FUTURE)
        q.data_mode = ops.QUARANTINE
        db.flush()
        live = list(db.scalars(
            select(OddsQuote).where(OddsQuote.data_mode == ops.LIVE)
        ))
        assert live == []

    def test_nothing_is_deleted(self, db: Session) -> None:
        """Kept in full, in another cohort. The standing instruction is not
        to delete development data."""
        _quote(db, observed_at=FUTURE)
        before = db.scalar(select(OddsQuote).where(OddsQuote.canonical_game_id == GAME))
        assert before is not None
        before.data_mode = ops.QUARANTINE
        db.flush()
        assert db.scalar(
            select(OddsQuote).where(OddsQuote.canonical_game_id == GAME)
        ) is not None
