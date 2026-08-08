"""How a market line is STORED, asserted through the real capture path.

This exists because its absence cost a day.

A spread arrives from the provider mirrored: -3 on the favourite, +3 on
the underdog. `_write_quote` negates the away point, so what lands in the
table is HOME-RELATIVE - both rows of a book carry the home team's number.
Nothing said so except one line of comment, and every consumer had to know
it by heart.

So a fixture was written seeding HOME -3 / AWAY +3, a shape the capture
path never produces. It made the consensus median look broken, produced a
"fix" for a bug that did not exist, and hid a real regression underneath:
matching the away side at `-median_line` selects nothing under
home-relative storage, so the fallback quietly became the only path. The
fixture agreed with every step of that, because it encoded the convention
the conclusion needed.

These tests go through `capture_odds` and read the rows back. They cannot
agree with a wrong assumption, because they never state one - the provider
payload goes in, and the stored values come out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import OddsQuote
from fde_api.db.models import Base
from fde_api.forward.cohort import ProviderMode
from fde_api.forward.odds import capture_odds
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.venues import seed_venues

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
OBSERVED = KICK - timedelta(days=1)
SCHEDULE_OBSERVED = KICK - timedelta(days=20)
GAME = "2026_02_KC_BUF"

_SCHEDULE_CSV = (
    b"game_id,season,game_type,week,gameday,gametime,away_team,home_team,"
    b"stadium_id,stadium,location\n"
    b"2026_02_KC_BUF,2026,REG,2,2026-09-13,12:00,KC,BUF,BUF00,Highmark Stadium,Home\n"
)


@pytest.fixture()
def fsession(tmp_path, monkeypatch) -> Session:
    """A private database with the governed venue table seeded.

    Deliberately local rather than shared: this module asserts what the
    capture path WRITES, so it must not inherit a session someone else has
    already put quotes into.
    """
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    seed_venues(session)
    return session


def _event(*, home_point: float, home_price: int, away_price: int,
           total: float) -> list[dict]:
    """A provider payload in the provider's own convention: mirrored points."""
    return [{
        "id": "evt1",
        "commence_time": KICK.isoformat().replace("+00:00", "Z"),
        "home_team": "Buffalo Bills",
        "away_team": "Kansas City Chiefs",
        "bookmakers": [{
            "key": "draftkings",
            "last_update": OBSERVED.isoformat(),
            "markets": [
                {"key": "spreads", "outcomes": [
                    {"name": "Buffalo Bills", "price": home_price, "point": home_point},
                    {"name": "Kansas City Chiefs", "price": away_price,
                     "point": -home_point}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": -110, "point": total},
                    {"name": "Under", "price": -110, "point": total}]},
            ],
        }],
    }]


def _capture(session: Session, payload: list[dict]) -> None:
    ingest_schedule(session, _SCHEDULE_CSV, season=2026,
                    observed_at=SCHEDULE_OBSERVED)
    capture_odds(session, payload, request_id="r",
                 provider_mode=ProviderMode.FIXTURE, observed_at=OBSERVED)
    session.commit()


def _lines(session: Session, market: str) -> dict[str, float]:
    return {
        q.selection: q.line
        for q in session.scalars(
            select(OddsQuote).where(OddsQuote.market == market))
        if q.line is not None
    }


class TestSpreadLinesAreStoredHomeRelative:
    def test_a_home_favourite_stores_the_same_number_on_both_rows(
        self, fsession: Session
    ) -> None:
        """Provider sends BUF -3 / KC +3. Both rows store -3."""
        _capture(fsession, _event(home_point=-3.0, home_price=-110,
                                  away_price=-110, total=44.5))
        lines = _lines(fsession, "SPREAD")
        assert lines["HOME"] == -3.0
        assert lines["AWAY"] == -3.0, (
            "the away row must carry the HOME number; a mirrored value here "
            "means two conventions share one column"
        )

    def test_a_home_underdog_stores_the_same_number_on_both_rows(
        self, fsession: Session
    ) -> None:
        """The sign has to survive, so the dog case is asserted separately."""
        _capture(fsession, _event(home_point=6.5, home_price=-110,
                                  away_price=-110, total=44.5))
        lines = _lines(fsession, "SPREAD")
        assert lines["HOME"] == 6.5
        assert lines["AWAY"] == 6.5

    def test_the_two_rows_are_never_mirrors_of_each_other(
        self, fsession: Session
    ) -> None:
        """The single property every consumer depends on."""
        _capture(fsession, _event(home_point=-7.5, home_price=-110,
                                  away_price=-110, total=44.5))
        lines = _lines(fsession, "SPREAD")
        assert lines["HOME"] == lines["AWAY"]
        assert lines["AWAY"] != -lines["HOME"]


class TestTotalLinesAreStoredAsGiven:
    def test_both_sides_carry_the_same_total(self, fsession: Session) -> None:
        """Totals are not mirrored by the provider and are not negated on
        write, so OVER and UNDER agree. Asserted rather than assumed: the
        consensus takes one line per book on exactly this basis."""
        _capture(fsession, _event(home_point=-3.0, home_price=-110,
                                  away_price=-110, total=44.5))
        lines = _lines(fsession, "TOTAL")
        assert lines["OVER"] == 44.5
        assert lines["UNDER"] == 44.5


class TestTheConventionMatchesWhatConsensusExpects:
    def test_the_consensus_line_equals_the_captured_home_line(
        self, fsession: Session
    ) -> None:
        """End to end: one book, one line, no arithmetic in between.

        With a single book the consensus is refused for want of a minimum,
        so this asserts against `select_eligible_quotes` and the stored
        rows instead - which is the part the convention actually governs.
        """
        _capture(fsession, _event(home_point=-3.0, home_price=-115,
                                  away_price=-105, total=44.5))
        from fde_api.forward.consensus import select_eligible_quotes

        quotes = list(fsession.scalars(
            select(OddsQuote).where(OddsQuote.market == "SPREAD")))
        eligible, _ = select_eligible_quotes(
            quotes, as_of_at=OBSERVED + timedelta(minutes=1),
            max_age_minutes=60, kickoff_utc=KICK)
        home_lines = [q.line for q in eligible if q.selection == "HOME"]
        away_lines = [q.line for q in eligible if q.selection == "AWAY"]
        assert home_lines == [-3.0]
        # The consensus matches BOTH sides on `median_line` itself. That is
        # only correct because the away row holds the same number.
        assert away_lines == home_lines


@pytest.mark.parametrize("home_point", [-14.0, -3.0, -0.5, 0.0, 2.5, 10.0])
def test_the_convention_holds_across_the_range(
    fsession: Session, home_point: float
) -> None:
    _capture(fsession, _event(home_point=home_point, home_price=-110,
                              away_price=-110, total=44.5))
    lines = _lines(fsession, "SPREAD")
    assert lines["HOME"] == home_point
    assert lines["AWAY"] == home_point
