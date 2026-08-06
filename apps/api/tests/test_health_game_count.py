"""The regular-season game count must count regular-season games.

`EXPECTED_2026_GAMES` is the REG count. Counting the whole slate against it
meant a correctly captured preseason fixture raised a CRITICAL claiming the
schedule was corrupt - and `schedule_game_count` suppresses candidates, so
one extra legitimate row would have suppressed every game in the season.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.health import EXPECTED_2026_GAMES, run_health_checks
from fde_api.forward.modes import DataMode

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _add(session: Session, n: int, season_type: str, offset: int = 0) -> None:
    for i in range(n):
        idx = i + offset
        session.add(ScheduleObservation(
            data_mode="LIVE_RESEARCH",
            canonical_game_id=f"2026_{season_type}_{idx:04d}",
            provider="nflverse" if season_type == "REG" else "espn",
            provider_game_id=str(idx), season=2026, season_type=season_type,
            week=1, home_team_id="ARI", away_team_id="CAR",
            kickoff_utc=datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
            neutral_site=False, international=False, game_status="SCHEDULED",
            content_hash=f"h{season_type}{idx}", source_manifest_version="t",
            source_updated_at=NOW, observed_at=NOW))
    session.commit()


def _check(session: Session) -> dict:
    report = run_health_checks(session, now=NOW, data_mode=DataMode.LIVE_RESEARCH)
    return next(c for c in report["checks"] if c["id"] == "schedule_game_count")


class TestTheCountIsRegularSeasonOnly:
    def test_a_full_regular_season_passes(self, session) -> None:
        _add(session, EXPECTED_2026_GAMES, "REG")
        assert _check(session)["status"] == "OK"

    def test_a_preseason_game_does_not_break_it(self, session) -> None:
        """The bug: 272 REG + 1 PRE reported 273 observed, expected 272."""
        _add(session, EXPECTED_2026_GAMES, "REG")
        _add(session, 1, "PRE", offset=9000)
        check = _check(session)
        assert check["status"] == "OK", check["explanation"]
        assert check["detail"]["regular_season"] == EXPECTED_2026_GAMES
        assert check["detail"]["other"] == 1

    def test_the_extra_games_are_still_reported(self, session) -> None:
        """Not counted against the total, but not hidden either."""
        _add(session, EXPECTED_2026_GAMES, "REG")
        _add(session, 3, "PRE", offset=9000)
        assert "+3 non-regular-season" in _check(session)["explanation"]

    def test_a_genuinely_missing_regular_season_game_still_fails(self, session) -> None:
        """The check must keep doing its job."""
        _add(session, EXPECTED_2026_GAMES - 1, "REG")
        _add(session, 5, "PRE", offset=9000)
        check = _check(session)
        assert check["status"] != "OK"
        assert str(EXPECTED_2026_GAMES - 1) in check["explanation"]
