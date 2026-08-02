"""Every capture must state when it observed what it captured.

`observed_at` used to default to `utc_now()` on the capture write paths.
A caller that omitted it stamped WALL-CLOCK time onto the record instead
of the scheduler's clock — so under a ReplayClock, replaying a historical
week would mark every observation as having been seen *now*. That is a
lookahead vector in the exact axis this engine's integrity rests on, and
it is invisible in production, where the scheduler runs on a SystemClock
and `utc_now()` happens to equal `ctx.now()`.

The defaults are gone. These tests pin that, because a default is the kind
of thing that gets restored for convenience.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from fde_api.forward.odds import capture_odds
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.weather import capture_forecast_for_game

CAPTURES = [capture_odds, ingest_schedule, capture_forecast_for_game]


class TestNoCaptureInventsItsObservationTime:
    @pytest.mark.parametrize("fn", CAPTURES, ids=lambda f: f.__name__)
    def test_observed_at_has_no_default(self, fn) -> None:
        param = inspect.signature(fn).parameters["observed_at"]
        assert param.default is inspect.Parameter.empty, (
            f"{fn.__name__} would silently stamp wall-clock time when a "
            "caller omits observed_at"
        )

    @pytest.mark.parametrize("fn", CAPTURES, ids=lambda f: f.__name__)
    def test_observed_at_is_keyword_only(self, fn) -> None:
        """Positional passing would let an argument land there by accident."""
        param = inspect.signature(fn).parameters["observed_at"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__

    @pytest.mark.parametrize("fn", CAPTURES, ids=lambda f: f.__name__)
    def test_omitting_it_fails_loudly(self, fn) -> None:
        """Supplying every other required argument and omitting this one
        must fail to bind. Calling `fn()` outright would only prove that
        the first parameter is missing, which says nothing about this one."""
        sig = inspect.signature(fn)
        supplied = {
            name: object()
            for name, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty and name != "observed_at"
        }
        with pytest.raises(TypeError, match="observed_at"):
            sig.bind(**supplied)


class TestSchedulerSuppliesItsOwnClock:
    def test_handlers_pass_ctx_now_rather_than_wall_clock(self) -> None:
        """The handlers are the production callers. If one of them used
        utc_now() the requirement above would be satisfied while the
        lookahead vector stayed open."""
        import fde_api.forward.handlers as handlers

        src = inspect.getsource(handlers)
        assert "observed_at=ctx.now()" in src
        # utc_now must not be how a handler decides an observation time.
        assert "observed_at=utc_now()" not in src

    def test_frozen_clock_time_is_what_lands_on_the_record(self, tmp_path, monkeypatch) -> None:
        """End to end: a scheduler on a frozen 2026 clock must stamp that
        instant, not today's date."""
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from fde_api.config import settings
        from fde_api.db.forward_models import OddsQuote
        from fde_api.db.models import Base
        from fde_api.forward.cohort import Cohort, ProviderMode
        from fde_api.forward.handlers import register_all
        from fde_api.forward.policy import build_policy_draft, freeze_policy
        from fde_api.forward.scheduler import FrozenClock, Scheduler
        from fde_api.forward.venues import seed_venues

        monkeypatch.setattr(settings, "data_dir", tmp_path)
        kick = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
        now = kick - timedelta(days=2)

        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, future=True)

        header = (
            "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,"
            "home_team,home_score,location,result,total,overtime,old_game_id,gsis,"
            "nfl_detail_id,pfr,pff,espn,ftn,away_rest,home_rest,away_moneyline,"
            "home_moneyline,spread_line,away_spread_odds,home_spread_odds,total_line,"
            "under_odds,over_odds,div_game,roof,surface,temp,wind,away_qb_id,home_qb_id,"
            "away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
        )
        row = dict.fromkeys(header.split(","), "")
        row.update({
            "game_id": "2026_02_KC_BUF", "season": "2026", "game_type": "REG", "week": "2",
            "gameday": "2026-09-13", "gametime": "13:00", "away_team": "KC",
            "home_team": "BUF", "location": "Home", "div_game": "0", "roof": "outdoors",
            "surface": "a_turf", "stadium_id": "BUF00", "stadium": "Highmark Stadium",
            "away_rest": "7", "home_rest": "7",
        })
        csv = ("\n".join([header, ",".join(row[k] for k in header.split(","))]) + "\n").encode()

        with factory() as s:
            seed_venues(s)
            freeze_policy(s, build_policy_draft(
                policy_version="ftp-2026-v1",
                start=datetime(2026, 9, 1).date(), end=datetime(2027, 2, 28).date(),
            ))
            ingest_schedule(s, csv, season=2026, observed_at=now - timedelta(days=10))
            s.commit()

        def book(key: str) -> dict:
            return {"key": key, "last_update": now.isoformat(), "markets": [
                {"key": "spreads", "outcomes": [
                    {"name": "Buffalo Bills", "price": -110, "point": -2.5},
                    {"name": "Kansas City Chiefs", "price": -110, "point": 2.5}]}]}

        payload = [{
            "id": "evt1", "commence_time": kick.isoformat(),
            "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs",
            "bookmakers": [book("draftkings"), book("fanduel"), book("betmgm")],
        }]

        sched = Scheduler(factory, clock=FrozenClock(now), cohort=Cohort.BURN_IN,
                          provider_mode=ProviderMode.FIXTURE, policy_version="ftp-2026-v1")
        register_all(sched)
        sched.run_job("odds_capture", slot=now, params={"fixture_payload": payload})

        with factory() as s:
            stamps = set(s.scalars(select(OddsQuote.observed_at)))
        assert stamps, "no quotes captured"
        assert stamps == {now}, stamps
