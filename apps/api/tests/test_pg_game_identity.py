from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fde_api.db.forward_models import GameIdentityBinding, ScheduleObservation
from fde_api.db.models import Base
from fde_api.forward.game_identity import resolve_game

pytest_plugins = ["pgconftest"]

pytestmark = pytest.mark.pg_identity


def test_cross_provider_first_arrival_race_cannot_publish_two_canonical_games(pg_engine):
    Base.metadata.create_all(pg_engine)
    barrier = Barrier(2)
    now = datetime(2099, 8, 1, tzinfo=UTC)

    def ingest(provider):
        with Session(pg_engine) as s:
            barrier.wait(timeout=20)
            try:
                gid = resolve_game(s, provider=provider, provider_game_id=f"race-{provider}",
                    data_mode="DEMO", season=2099, season_type="PRE", home="DEN", away="SEA",
                    week=0, kickoff=now, proposed_id=f"RACE_{provider}", observed_at=now)
                if gid:
                    s.add(ScheduleObservation(data_mode="DEMO", canonical_game_id=gid,
                        provider=provider, provider_game_id=f"race-{provider}", season=2099,
                        season_type="PRE", week=0, home_team_id="DEN", away_team_id="SEA",
                        kickoff_utc=now, game_status="SCHEDULED", content_hash=provider, observed_at=now))
                s.commit()
                return gid
            except IntegrityError:
                s.rollback()
                return None  # No inconsistent binding is committed; caller can retry.

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(ingest, ["espn", "nflverse"]))
    with Session(pg_engine) as s:
        rows = list(s.scalars(select(ScheduleObservation).where(ScheduleObservation.season == 2099)))
        assert len({r.canonical_game_id for r in rows}) == 1
        canonical = rows[0].canonical_game_id
        assert {gid for gid in outcomes if gid} == {canonical}
        for provider in ("espn", "nflverse"):
            assert resolve_game(s, provider=provider, provider_game_id=f"race-{provider}",
                data_mode="DEMO", season=2099, season_type="PRE", home="DEN", away="SEA",
                week=0, kickoff=now, proposed_id=f"RACE_{provider}", observed_at=now) == canonical
        s.commit()
        assert len(list(s.scalars(select(GameIdentityBinding).where(
            GameIdentityBinding.canonical_game_id == canonical)))) == 3
