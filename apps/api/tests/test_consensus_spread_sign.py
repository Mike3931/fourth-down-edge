"""The spread consensus, under the convention the capture path writes.

SPREAD lines are stored HOME-RELATIVE: `_write_quote` negates the away
point, so both rows of a book carry the same number. Every test here seeds
that way, because seeding the provider's raw mirrored values describes
data the system never actually holds - and a test built on a convention
the code does not use will happily confirm the wrong behaviour.

That is not hypothetical. An earlier version of this file seeded HOME -3 /
AWAY +3, concluded the median was broken, and passed against a change that
matched the away side at `-median_line` - which under home-relative
storage matches nothing at all, silently turning the `or away_q` fallback
into the only path.

Both sides therefore match on `median_line` itself, and the median is
taken over one row per book so the dispersion means something.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import OddsQuote, ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort
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
        # Home-relative, as `_write_quote` stores it: the away row carries
        # the home team's number, not its own mirror.
        for selection, point in (("HOME", home_line), ("AWAY", home_line)):
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
        kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=DataMode.LIVE_RESEARCH)
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


class TestTheTotalIsAlsoOneLinePerBook:
    """Both sides of a total carry the same number.

    Counting every row weights a book that published both sides twice as
    heavily as one that published a single side. Where publication is even
    that is harmless - equal duplication moves neither the median nor the
    population sd - which is why it survived: the common case hides it.

    Where publication is uneven the median migrates towards the two-sided
    books, and the result is a consensus total no book offered.
    """

    def _seed_totals(self, session, books: list[tuple[str, float, tuple[str, ...]]]) -> None:
        session.add(ScheduleObservation(
            data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
            provider_game_id="x", season=2026, season_type="REG", week=1,
            home_team_id="ARI", away_team_id="CAR", kickoff_utc=KICK,
            neutral_site=False, international=False, game_status="SCHEDULED",
            content_hash="t", source_manifest_version="t",
            source_updated_at=NOW, observed_at=NOW))
        for book, total, sides in books:
            for selection in sides:
                session.add(OddsQuote(
                    data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
                    provider_mode="LIVE", sportsbook=book, market="TOTAL",
                    selection=selection, line=total, american=-110,
                    decimal_odds=1.91, is_live=False,
                    observed_at=NOW - timedelta(minutes=5),
                    raw_hash=hashlib.sha256(
                        f"{book}{selection}{total}".encode()).hexdigest()))
        session.commit()

    def _total(self, session):
        snap, _ = build_consensus(
            session, canonical_game_id=GAME, market="TOTAL", as_of_at=NOW,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=DataMode.LIVE_RESEARCH)
        return snap

    def test_evenly_published_totals_are_unchanged(self, session) -> None:
        """The common case, and the reason this went unnoticed."""
        self._seed_totals(session, [
            ("draftkings", 34.5, ("OVER", "UNDER")),
            ("fanduel", 35.5, ("OVER", "UNDER")),
            ("betmgm", 36.5, ("OVER", "UNDER")),
        ])
        snap = self._total(session)
        assert snap is not None
        assert snap.median_line == 35.5

    def test_unevenly_published_totals_no_longer_skew(self, session) -> None:
        """Books offered 34.5, 34.5, 40.5, 40.5 - a median of 37.5.

        Counting rows returned 34.5, because the two-sided books
        contributed four values against the one-sided books' two.
        """
        self._seed_totals(session, [
            ("draftkings", 34.5, ("OVER", "UNDER")),
            ("fanduel", 34.5, ("OVER", "UNDER")),
            ("betmgm", 40.5, ("OVER",)),
            ("caesars", 40.5, ("OVER",)),
        ])
        snap = self._total(session)
        assert snap is not None
        assert snap.median_line == 37.5

    def test_agreeing_books_show_no_total_dispersion(self, session) -> None:
        self._seed_totals(session, [
            ("draftkings", 44.5, ("OVER", "UNDER")),
            ("fanduel", 44.5, ("OVER", "UNDER")),
            ("betmgm", 44.5, ("OVER",)),
        ])
        snap = self._total(session)
        assert snap is not None
        assert snap.line_dispersion == pytest.approx(0.0)


class TestTheAwaySideIsPricedAtTheConsensusLine:
    """The at-line filter must actually select something.

    Matching the away side at `-median_line` under home-relative storage
    selects nothing, and the `or away_q` fallback silently becomes the only
    path - pricing the away side across every line on offer while appearing
    to price it at the consensus. The median is robust enough that the
    resulting number is often still right, which is exactly why this needs
    testing at the filter rather than at the output.
    """

    def _seed_with_prices(self, session, books) -> None:
        session.add(ScheduleObservation(
            data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
            provider_game_id="x", season=2026, season_type="REG", week=1,
            home_team_id="SEA", away_team_id="NE", kickoff_utc=KICK,
            neutral_site=False, international=False, game_status="SCHEDULED",
            content_hash="t", source_manifest_version="t",
            source_updated_at=NOW, observed_at=NOW))
        for book, line, home_price, away_price in books:
            for selection, price in (("HOME", home_price), ("AWAY", away_price)):
                session.add(OddsQuote(
                    data_mode="LIVE_RESEARCH", canonical_game_id=GAME, provider="t",
                    provider_mode="LIVE", sportsbook=book, market="SPREAD",
                    selection=selection, line=line, american=price,
                    decimal_odds=1.9, is_live=False,
                    observed_at=NOW - timedelta(minutes=5),
                    raw_hash=hashlib.sha256(
                        f"{book}{selection}{line}".encode()).hexdigest()))
        session.commit()

    def test_the_away_price_ignores_books_off_the_consensus_line(
        self, session
    ) -> None:
        """Prices chosen so the two paths cannot agree by luck.

        Three books at the -3.0 consensus priced -200/-150/-100, and one
        outlier at -9.0 priced +500. At the line the median is -150;
        falling through to all four away quotes gives -125. An earlier
        version of this test used prices whose medians coincided, so it
        passed against the broken filter and proved nothing - the median is
        robust enough that a fall-through is usually invisible in the
        output, which is why the case has to be built to expose it.
        """
        self._seed_with_prices(session, [
            ("draftkings", -3.0, -110, -200),
            ("fanduel", -3.0, -110, -150),
            ("betmgm", -3.0, -110, -100),
            ("caesars", -9.0, -110, 500),
        ])
        snap = _consensus(session)
        assert snap is not None
        assert snap.median_line == -3.0
        assert snap.away_price_american == -150, (
            "fell through to every away quote; -125 is the all-books median"
        )

    def test_the_home_and_away_filters_select_the_same_books(
        self, session
    ) -> None:
        """Under home-relative storage both sides carry the same number, so
        a filter that works for one has to work for the other."""
        self._seed_with_prices(session, [
            ("draftkings", -2.5, -108, -112),
            ("fanduel", -2.5, -108, -112),
            ("betmgm", -6.0, 200, -400),
        ])
        snap = _consensus(session)
        assert snap is not None
        assert snap.median_line == -2.5
        assert snap.home_price_american == -108
        assert snap.away_price_american == -112
