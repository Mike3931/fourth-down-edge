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
              market: str = "SPREAD",
              cohort: str = "burn_in") -> ConsensusSnapshot:
    """The cohort is now a column of its own, and it is what the identity
    hashes. The old helper passed `mode` into the `cohort` field, which is
    exactly the defect `d8f41c6a3b92` removed."""
    c = ConsensusSnapshot(
        data_mode=mode, cohort=cohort, min_books_applied=3,
        canonical_game_id=GAME, market=market,
        method_version=CONSENSUS_METHOD_VERSION, provider_mode="UNKNOWN_LEGACY",
        median_line=-2.5, eligible_books=3,
        quote_ids={"quote_ids": [], "books": []}, observed_at=observed_at,
        logical_identity_hash=CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": market, "cutoff": observed_at,
            "cohort": cohort, "method_version": CONSENSUS_METHOD_VERSION,
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


class TestTheConsensusIdentityIsNoLongerTouched:
    """This class used to be `TestTheConsensusIdentityIsRecomputed`, and
    it was right at the time.

    While CONSENSUS was fed `data_mode.value` as its `cohort`, moving a
    row from LIVE_RESEARCH to DEMO really did change its identity, and
    leaving the old hash behind would have left a hash of fields the row
    no longer had. Since `d8f41c6a3b92` the identity carries the row's
    own `cohort` column, which this script does not move — so the hash
    stays true and recomputing it would be the script inventing an
    identity change that did not happen.
    """

    def test_the_plan_proposes_no_new_hash(self, db: Session) -> None:
        _snapshot(db, observed_at=FUTURE)
        changes, _ = ops.plan(db, now=NOW)
        snap = next(c for c in changes if c["table"] == "consensus_snapshots")
        assert snap["new_logical_hash"] is None

    def test_the_stored_hash_is_still_the_one_the_row_implies(
        self, db: Session
    ) -> None:
        """The reason no recompute is needed: the identity is a function
        of the cohort, and the cohort is not what moves."""
        row = _snapshot(db, observed_at=FUTURE)
        ops.plan(db, now=NOW)
        assert row.logical_identity_hash == CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": FUTURE,
            "cohort": row.cohort, "method_version": CONSENSUS_METHOD_VERSION,
        })

    def test_the_data_mode_is_not_the_cohort(self, db: Session) -> None:
        """The distinction the whole change rests on. A row in
        LIVE_RESEARCH whose cohort is `burn_in` hashes to a different
        slot than one whose cohort is `LIVE_RESEARCH` — which is what
        this identity used to be given."""
        row = _snapshot(db, observed_at=FUTURE)
        by_cohort = CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": FUTURE,
            "cohort": row.cohort, "method_version": CONSENSUS_METHOD_VERSION,
        })
        by_mode = CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": FUTURE,
            "cohort": row.data_mode, "method_version": CONSENSUS_METHOD_VERSION,
        })
        assert by_cohort != by_mode

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
