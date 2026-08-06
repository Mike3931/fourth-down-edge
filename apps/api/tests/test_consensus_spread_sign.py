"""The spread consensus must be measured from one side, not both.

A spread arrives as the provider gives it: -3 on the favourite and +3 on
the underdog, one row each. Taking the median across BOTH rows measures
the sign convention rather than the market, and it fails in the worst
possible way - silently, with a plausible-looking number.

Three books all posting home -3 produced:

    median_line     0.0     (there is no 0.0 spread anywhere in the market)
    line_dispersion 3.0     (reads as total disagreement; they agree exactly)

`median_line` is what CLV is measured against, so a systematically wrong
consensus line would have quietly corrupted every closing-line comparison
in the forward test.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import OddsQuote, ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.consensus import build_consensus
from fde_api.forward.modes import DataMode

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
KICK = NOW + timedelta(hours=3)
GAME = "SPREAD_SIGN"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _seed(session, per_book: list[tuple[str, float]], price: int = -110) -> None:
    session.add(ScheduleObservation(
        data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
        provider_game_id="x", season=2026, season_type="REG", week=1,
        home_team_id="ARI", away_team_id="CAR", kickoff_utc=KICK,
        neutral_site=False, international=False, game_status="SCHEDULED",
        content_hash="t", source_manifest_version="t",
        source_updated_at=NOW, observed_at=NOW))
    for book, home_line in per_book:
        # Exactly how a provider reports it: mirrored points, one row a side.
        for selection, point in (("HOME", home_line), ("AWAY", -home_line)):
            session.add(OddsQuote(
                data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
                provider_mode="LIVE", sportsbook=book, market="SPREAD",
                selection=selection, line=point, american=price,
                decimal_odds=1.91, is_live=False,
                observed_at=NOW - timedelta(minutes=5),
                raw_hash=hashlib.sha256(f"{book}{selection}{point}".encode()).hexdigest()))
    session.commit()


def _consensus(session):
    snap, _ = build_consensus(
        session, canonical_game_id=GAME, market="SPREAD", as_of_at=NOW,
        kickoff_utc=KICK, data_mode=DataMode.LIVE_RESEARCH)
    return snap


class TestTheConsensusLineIsOnTheHomeSide:
    def test_unanimous_home_favourite(self, session) -> None:
        _seed(session, [("draftkings", -3.0), ("fanduel", -3.0), ("betmgm", -3.0)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.median_line == -3.0, "the bug returned 0.0 here"

    def test_unanimous_home_underdog(self, session) -> None:
        """The sign must survive: a home dog is a positive number."""
        _seed(session, [("draftkings", 6.5), ("fanduel", 6.5), ("betmgm", 6.5)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.median_line == 6.5

    def test_the_median_is_taken_across_books(self, session) -> None:
        _seed(session, [("draftkings", -3.0), ("fanduel", -3.5), ("betmgm", -4.0)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.median_line == -3.5


class TestDispersionMeasuresDisagreementNotConvention:
    def test_agreeing_books_show_no_dispersion(self, session) -> None:
        """The bug reported 3.0 for books that agreed exactly - the spread
        between the two sides, mistaken for disagreement between books."""
        _seed(session, [("draftkings", -3.0), ("fanduel", -3.0), ("betmgm", -3.0)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.line_dispersion == pytest.approx(0.0)

    def test_disagreeing_books_show_dispersion(self, session) -> None:
        _seed(session, [("draftkings", -3.0), ("fanduel", -3.5), ("betmgm", -4.0)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.line_dispersion > 0.0

    def test_dispersion_is_not_inflated_by_the_mirror(self, session) -> None:
        """One line per book, not two.

        Home lines -3/-4/-5 have a population sd of 0.816. Folding the away
        mirrors in gives -5..+5 about a mean of zero and a sd of 4.08 - five
        times the real disagreement, from data that says the same thing.
        """
        _seed(session, [("draftkings", -3.0), ("fanduel", -4.0), ("betmgm", -5.0)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.line_dispersion == pytest.approx(0.8165, abs=1e-3)


class TestPricingHappensAtTheConsensusLine:
    def test_the_away_side_is_matched_at_its_own_mirror(self, session) -> None:
        """The away row for a home -3 consensus is +3, not -3. Matching the
        away side against the home number finds nothing and silently falls
        back to every away quote regardless of line."""
        _seed(session, [("draftkings", -3.0), ("fanduel", -3.0), ("betmgm", -3.0)])
        snap = _consensus(session)
        assert snap is not None
        assert snap.home_price_american == -110
        assert snap.away_price_american == -110
        assert snap.no_vig_home_prob == pytest.approx(0.5)
