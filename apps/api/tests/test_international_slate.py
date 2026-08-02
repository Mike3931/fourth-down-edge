"""Regression tests for the 2026 international slate.

The published 2026 schedule contains NINE international games across
EIGHT stadiums (Tottenham Hotspur Stadium hosts two). An earlier build
counted eight because it detected international games from the source's
`location` flag, which reads "Home" for a club's designated home game
played abroad. These tests pin the correct counts and the venue-driven
detection rule.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from fde_api.config import settings
from fde_api.db.models import Base
from fde_api.forward.schedule import current_slate, ingest_schedule
from fde_api.forward.venues import VENUES_BY_ID, is_international, resolve_venue, seed_venues
from fde_api.raw import RawArtifactStore

EXPECTED_INTERNATIONAL_GAMES = 9
EXPECTED_INTERNATIONAL_STADIUMS = 8

# (game_id, venue_id, country, source `location` value)
EXPECTED_SLATE = {
    "2026_01_SF_LA": ("INT_MCG", "AU", "Neutral"),
    "2026_03_BAL_DAL": ("INT_MARACANA", "BR", "Neutral"),
    "2026_04_IND_WAS": ("INT_TOTTENHAM", "GB", "Neutral"),
    "2026_05_PHI_JAX": ("INT_TOTTENHAM", "GB", "Home"),  # designated home game, played abroad
    "2026_06_HOU_JAX": ("INT_WEMBLEY", "GB", "Neutral"),
    "2026_07_PIT_NO": ("INT_STADE_FRANCE", "FR", "Neutral"),
    "2026_09_CIN_ATL": ("INT_BERNABEU", "ES", "Neutral"),
    "2026_10_NE_DET": ("INT_ALLIANZ", "DE", "Neutral"),
    "2026_11_MIN_SF": ("INT_AZTECA", "MX", "Neutral"),
}

DOMESTIC_VENUE_IDS = {v.id for v in VENUES_BY_ID.values() if v.country == "US"}


def _published_schedule() -> bytes | None:
    store = RawArtifactStore(settings.raw_dir)
    m = store.latest_success("nflverse", "games")
    return store.artifact_path(m).read_bytes() if m else None


@pytest.fixture()
def slate_session() -> Session:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    seed_venues(s)
    payload = _published_schedule()
    if payload is None:
        pytest.skip("no ingested nflverse schedule artifact available")
    ingest_schedule(s, payload, season=2026, observed_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    return s


class TestNineInternationalGames:
    def test_exactly_nine_international_games(self, slate_session: Session) -> None:
        intl = [g for g in current_slate(slate_session, 2026) if g.international]
        assert len(intl) == EXPECTED_INTERNATIONAL_GAMES

    def test_exactly_eight_international_stadiums(self, slate_session: Session) -> None:
        intl = [g for g in current_slate(slate_session, 2026) if g.international]
        assert len({g.stadium_id for g in intl}) == EXPECTED_INTERNATIONAL_STADIUMS

    def test_tottenham_hosts_two(self, slate_session: Session) -> None:
        intl = [g for g in current_slate(slate_session, 2026) if g.international]
        tottenham = [g for g in intl if g.stadium_id == "INT_TOTTENHAM"]
        assert {g.canonical_game_id for g in tottenham} == {"2026_04_IND_WAS", "2026_05_PHI_JAX"}

    def test_exact_slate_membership_and_venues(self, slate_session: Session) -> None:
        intl = {g.canonical_game_id: g for g in current_slate(slate_session, 2026) if g.international}
        assert set(intl) == set(EXPECTED_SLATE)
        for gid, (venue_id, country, _loc) in EXPECTED_SLATE.items():
            assert intl[gid].stadium_id == venue_id, gid
            assert VENUES_BY_ID[venue_id].country == country, gid

    def test_designated_home_game_abroad_is_international(self, slate_session: Session) -> None:
        """The regression that caused the miscount: location='Home', venue in GB."""
        intl = {g.canonical_game_id: g for g in current_slate(slate_session, 2026) if g.international}
        jax = intl["2026_05_PHI_JAX"]
        assert jax.international is True
        assert jax.neutral_site is False  # league designation preserved
        assert jax.stadium_id == "INT_TOTTENHAM"
        assert jax.venue_timezone == "Europe/London"

    def test_no_international_game_keeps_domestic_venue(self, slate_session: Session) -> None:
        intl = [g for g in current_slate(slate_session, 2026) if g.international]
        leaked = [g.canonical_game_id for g in intl if g.stadium_id in DOMESTIC_VENUE_IDS]
        assert leaked == []

    def test_international_games_carry_venue_timezone_not_us(self, slate_session: Session) -> None:
        intl = [g for g in current_slate(slate_session, 2026) if g.international]
        for g in intl:
            assert g.venue_timezone is not None
            assert not g.venue_timezone.startswith("America/New_York")

    def test_source_location_flag_alone_undercounts(self) -> None:
        """Proves the old rule was wrong, so it cannot be reintroduced."""
        payload = _published_schedule()
        if payload is None:
            pytest.skip("no schedule artifact")
        rows = [r for r in csv.DictReader(io.StringIO(payload.decode())) if r["season"] == "2026"]
        by_flag = [r for r in rows if r["location"].strip().lower() == "neutral"]
        assert len(by_flag) == EXPECTED_INTERNATIONAL_GAMES - 1  # the flag misses one

    def test_venue_resolution_is_name_first(self) -> None:
        # Home-designated game abroad still resolves to the overseas venue.
        v = resolve_venue(stadium_id="JAX00", stadium_name="Tottenham Hotspur Stadium", neutral_site=False)
        assert v is not None and v.id == "INT_TOTTENHAM" and is_international(v)
        # Ordinary domestic game is unaffected.
        d = resolve_venue(stadium_id="JAX00", stadium_name="EverBank Stadium", neutral_site=False)
        assert d is not None and d.id == "JAX00" and not is_international(d)

    def test_declared_neutral_unknown_venue_does_not_fall_back(self) -> None:
        """Refusing to guess beats silently using the wrong stadium."""
        assert resolve_venue(stadium_id="JAX00", stadium_name="Unknown Ground", neutral_site=True) is None
