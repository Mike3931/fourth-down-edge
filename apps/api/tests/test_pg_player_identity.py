from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from fde_api.db.models import Player
from fde_api.db.research_models import PlayerBinding, PlayerIdentityReview
from fde_api.forward.intelligence import bind_players

pytest_plugins = ["pgconftest"]
pytestmark = pytest.mark.pg_identity


def test_concurrent_player_binding_cannot_reassign_provider_identity(pg_engine):
    now = datetime(2099, 1, 1, tzinfo=UTC)
    with Session(pg_engine) as s:
        s.add_all([Player(id="RACE_PLAYER_1", name="Same", birth_date="1995-01-01"),
                   Player(id="RACE_PLAYER_2", name="Same", birth_date="1995-01-01")])
        s.commit()
    barrier = Barrier(2)
    def worker(pid):
        with Session(pg_engine) as s:
            event.listen(s, "before_flush", lambda *args: barrier.wait(timeout=20), once=True)
            try:
                bind_players(s, [{"gsis_id": pid, "espn_id": "PLAYER_RACE_ESPN",
                                 "birth_date": "1995-01-01"}], "race-evidence", now)
                s.commit()
                return True
            except IntegrityError:
                s.rollback()
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ["RACE_PLAYER_1", "RACE_PLAYER_2"]))
    assert sorted(results) == [False, True]
    with Session(pg_engine) as s:
        binding = s.get(PlayerBinding, ("espn", "PLAYER_RACE_ESPN"))
        original = binding.player_id
        other = "RACE_PLAYER_2" if original == "RACE_PLAYER_1" else "RACE_PLAYER_1"
        bind_players(s, [{"gsis_id": other, "espn_id": "PLAYER_RACE_ESPN",
                         "birth_date": "1995-01-01"}], "retry-evidence", now)
        s.commit()
        assert s.get(PlayerBinding, ("espn", "PLAYER_RACE_ESPN")).player_id == original
        assert list(s.scalars(select(PlayerIdentityReview).where(
            PlayerIdentityReview.reason == "PROVIDER_ID_COLLISION")))
    with pg_engine.connect() as c:
        with pytest.raises(DBAPIError, match="append-only"):
            c.execute(text("UPDATE research_player_bindings SET player_id='FORGED' WHERE provider_id='PLAYER_RACE_ESPN'"))
        c.rollback()
