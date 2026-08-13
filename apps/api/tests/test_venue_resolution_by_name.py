"""A provider that gives a venue NAME and no stadium id.

`resolve_venue` resolved by name against the NEUTRAL registry — which
holds overseas venues and one US neutral site — and otherwise by
`stadium_id` against the domestic table. nflverse supplies that id. ESPN
does not: it gives `venue.fullName` and nothing else.

So every domestic ESPN game resolved to None. Fourteen of them, reported
by `unmapped_venues`, and the consequence runs further than a blank
column: `weather_capture` needs a Venue to get coordinates, so it records
NOT_APPLICABLE with "venue unresolved" and the NWS path never runs at all.
`weather_freshness` has read "no vintage captured yet" since the day it
was written.

Name is NOT a key, which is why this needs care rather than a dictionary.
`LAX01` and `LAX97` are both "SoFi Stadium" — the Rams' id and the
Chargers' id for one building. Resolving that by name has to be a
deliberate decision about what to do with a collision, not an arbitrary
`[0]`.
"""

from __future__ import annotations

import pytest

from fde_api.forward.venues import (
    VENUES_BY_ID,
    VenueSpec,
    is_international,
    resolve_by_name,
    resolve_venue,
)


def _resolve(name: str, *, stadium_id: str | None = None, neutral: bool = False):
    return resolve_venue(
        stadium_id=stadium_id, stadium_name=name, neutral_site=neutral
    )


class TestADomesticNameResolvesWithoutAnId:
    @pytest.mark.parametrize(
        ("name", "expected_id"),
        [
            ("Mercedes-Benz Stadium", "ATL97"),
            ("M&T Bank Stadium", "BAL00"),
            ("Highmark Stadium", "BUF00"),
            ("Soldier Field", "CHI98"),
            ("NRG Stadium", "HOU00"),
            ("Allegiant Stadium", "VEG00"),
            ("Caesars Superdome", "NOR00"),
            ("MetLife Stadium", "NYC01"),
            ("Lumen Field", "SEA00"),
            ("Levi's Stadium", "SFO01"),
            ("Northwest Stadium", "WAS00"),
        ],
    )
    def test_the_espn_names_captured_on_2026_08_12(
        self, name: str, expected_id: str
    ) -> None:
        """Every domestic venue the real capture actually produced."""
        venue = _resolve(name)
        assert venue is not None, f"{name} did not resolve"
        assert venue.id == expected_id

    def test_a_name_the_provider_writes_differently_still_resolves(self) -> None:
        """ESPN says "GEHA Field at Arrowhead Stadium"; the governed table
        says "GEHA Field at Arrowhead". A sponsor name with a trailing
        "Stadium" is not a different building."""
        venue = _resolve("GEHA Field at Arrowhead Stadium")
        assert venue is not None and venue.id == "KAN00"

    def test_the_hall_of_fame_venue_resolves(self) -> None:
        """Canton hosts a real NFL game every August, outdoors, and the
        governed table did not have it at all."""
        venue = _resolve("Tom Benson Hall of Fame Stadium", neutral=True)
        assert venue is not None
        assert venue.country == "US"
        assert venue.weather_applicable, "an outdoor venue needs a forecast"
        assert not is_international(venue), "Canton is not an overseas game"

    def test_an_unknown_name_still_refuses(self) -> None:
        """Refusing is the point. A guess would put a game at the wrong
        coordinates and forecast the wrong weather for it."""
        assert _resolve("Somewhere Nobody Governed") is None


class TestACollidingNameIsHandledDeliberately:
    def test_sofi_resolves_because_both_ids_are_the_same_building(self) -> None:
        """LAX01 and LAX97 are byte-identical apart from the id: the Rams'
        and the Chargers' nflverse ids for one stadium. Every field
        resolution exists to supply — coordinates, roof, timezone,
        elevation — agrees, so the collision is nominal."""
        venue = _resolve("SoFi Stadium")
        assert venue is not None
        assert venue.id in {"LAX01", "LAX97"}

    def test_it_is_deterministic_across_calls(self) -> None:
        assert len({_resolve("SoFi Stadium").id for _ in range(10)}) == 1

    def test_a_collision_whose_specs_disagree_refuses(self) -> None:
        """The guard. If two ids ever share a name AND differ on anything
        that matters, picking one silently would forecast one stadium's
        weather for a game at another."""
        a = VenueSpec("AAA00", "Twin Name Park", 40.0, -80.0, "America/New_York",
                      "OUTDOOR", "grass", 100)
        b = VenueSpec("BBB00", "Twin Name Park", 25.0, -95.0, "America/Chicago",
                      "DOME", "turf", 20)
        assert resolve_by_name("Twin Name Park", registry=(a, b)) is None

    def test_a_collision_that_agrees_is_allowed(self) -> None:
        a = VenueSpec("AAA00", "Same Park", 40.0, -80.0, "America/New_York",
                      "OUTDOOR", "grass", 100)
        b = VenueSpec("BBB00", "Same Park", 40.0, -80.0, "America/New_York",
                      "OUTDOOR", "grass", 100)
        got = resolve_by_name("Same Park", registry=(a, b))
        assert got is not None and got.id == "AAA00", "ties break on the lowest id"


class TestNothingAboutTheExistingPathsChanged:
    def test_a_stadium_id_still_wins_over_the_name(self) -> None:
        """nflverse supplies the id and it is authoritative. The name
        fallback exists for providers that supply no id, and must never
        override one that was given."""
        venue = _resolve("Soldier Field", stadium_id="BAL00")
        assert venue is not None and venue.id == "BAL00"

    def test_an_international_neutral_game_still_resolves_by_name_first(
        self,
    ) -> None:
        """The behaviour the docstring in `resolve_venue` exists for: a
        neutral game carries the HOME CLUB's stadium_id, and the name must
        win over it."""
        venue = _resolve("Melbourne Cricket Ground", stadium_id="LAX01",
                         neutral=True)
        assert venue is not None and venue.id == "INT_MCG"
        assert is_international(venue)

    def test_a_declared_neutral_site_with_an_unknown_venue_still_refuses(
        self,
    ) -> None:
        """Falling back to the home club's stadium for a neutral game is
        the specific error `resolve_venue` was written to prevent."""
        assert _resolve("Unknown Neutral Ground", stadium_id="LAX01",
                        neutral=True) is None

    def test_every_governed_id_still_resolves_by_id(self) -> None:
        for vid in VENUES_BY_ID:
            assert _resolve("", stadium_id=vid) is not None, vid


class TestTranscribingTheResolutionOntoExistingRows:
    """An improved mapping cannot reach rows already written.

    A schedule observation's `content_hash` covers what the SOURCE said —
    game, teams, kickoff, venue NAME, neutral flag, season type — and
    deliberately not `stadium_id`, which is our resolution rather than the
    provider's statement. So re-running the capture after improving the
    mapping writes nothing: same payload, same hash, no new observation.

    `scripts/resolve_recorded_venues.py` fills the gap in, on the same
    footing as `fix_espn_spread_convention.py`: the provider said
    "Mercedes-Benz Stadium" and still does, and only our transcription of
    that into a governed id was missing. It never overwrites an id that is
    already there, because that would be revising an answer rather than
    supplying a missing one.
    """

    @staticmethod
    def _plan(session):
        import importlib.util
        import pathlib
        import sys

        path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "resolve_recorded_venues.py"
        spec = importlib.util.spec_from_file_location("_resolve_venues", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["_resolve_venues"] = module
        spec.loader.exec_module(module)
        return module.plan(session)

    @pytest.fixture()
    def db(self, tmp_path, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from fde_api.db.models import Base

        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine, future=True)() as s:
            yield s

    @staticmethod
    def _obs(db, *, game: str, name: str, stadium_id: str | None, neutral: bool = False):
        from datetime import UTC, datetime

        from fde_api.db.forward_models import ScheduleObservation

        row = ScheduleObservation(
            canonical_game_id=game, data_mode="LIVE_RESEARCH", season=2026,
            season_type="PRE", week=0, away_team_id="AA", home_team_id="BB",
            kickoff_utc=datetime(2026, 8, 14, 23, 0, tzinfo=UTC),
            game_status="SCHEDULED", provider="espn",
            provider_game_id=f"espn:{game}", content_hash=f"h-{game}",
            observed_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
            stadium_id=stadium_id, stadium_name=name, neutral_site=neutral,
        )
        db.add(row)
        db.flush()
        return row

    def test_a_null_id_whose_name_resolves_is_filled_in(self, db) -> None:
        self._obs(db, game="G1", name="Mercedes-Benz Stadium", stadium_id=None)
        changes, _ = self._plan(db)
        assert [(c["game"], c["to"]) for c in changes] == [("G1", "ATL97")]

    def test_an_existing_id_is_never_overwritten(self, db) -> None:
        """Supplying a missing answer is a transcription. Replacing one is
        a revision, and needs a person."""
        self._obs(db, game="G2", name="Mercedes-Benz Stadium", stadium_id="BAL00")
        changes, refusals = self._plan(db)
        assert changes == []
        assert any("revision, not a transcription" in r for r in refusals)

    def test_a_name_that_still_does_not_resolve_is_reported(self, db) -> None:
        self._obs(db, game="G3", name="Nowhere Governed", stadium_id=None)
        changes, refusals = self._plan(db)
        assert changes == []
        assert any("still" in r and "does not resolve" in r for r in refusals)

    def test_an_agreeing_id_is_left_alone_and_not_reported(self, db) -> None:
        self._obs(db, game="G4", name="Mercedes-Benz Stadium", stadium_id="ATL97")
        changes, refusals = self._plan(db)
        assert changes == [] and refusals == []

    def test_running_it_twice_finds_nothing_the_second_time(self, db) -> None:
        row = self._obs(db, game="G5", name="Lumen Field", stadium_id=None)
        changes, _ = self._plan(db)
        assert len(changes) == 1
        row.stadium_id = changes[0]["to"]
        db.flush()
        assert self._plan(db)[0] == []
