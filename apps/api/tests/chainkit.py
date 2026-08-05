"""Shared harness for the record-chain tests.

Deliberately NOT a `test_*` module: it holds the fixture data, the game
definition, and the driver that walks one game through the scheduler, so
three test modules can share them without importing each other. A test
module that imports another test module's fixtures shadows them, which is
both a lint error and a real source of confusion about which fixture ran.

The pytest fixtures themselves live in `conftest.py`, where pytest can find
them by name without any import at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import register_all
from fde_api.forward.modes import DataMode
from fde_api.forward.scheduler import FrozenClock, Scheduler

# A deterministic U.S. outdoor game at a governed venue.
KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
GAME = "2026_02_KC_BUF"
DATA_MODE = DataMode.DEMO  # fixture cohort never claims LIVE_RESEARCH

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def csv_bytes(*, status_fields: dict[str, str] | None = None) -> bytes:
    base = dict.fromkeys(HEADER.split(","), "")
    base.update({
        "game_id": GAME, "season": "2026", "game_type": "REG", "week": "2",
        "gameday": "2026-09-13", "gametime": "13:00", "away_team": "KC", "home_team": "BUF",
        "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
        "stadium_id": "BUF00", "stadium": "Highmark Stadium", "away_rest": "7", "home_rest": "7",
        "home_qb_name": "Josh Allen", "away_qb_name": "Patrick Mahomes",
    })
    base.update(status_fields or {})
    return ("\n".join([HEADER, ",".join(base[k] for k in HEADER.split(","))]) + "\n").encode()


def payload(ts: datetime, *, point: float = -2.5) -> list[dict]:
    """Three eligible books, so consensus has something to agree about."""

    def book(key: str, adj: float) -> dict:
        return {"key": key, "last_update": ts.isoformat(), "markets": [
            {"key": "spreads", "outcomes": [
                {"name": "Buffalo Bills", "price": -110, "point": point + adj},
                {"name": "Kansas City Chiefs", "price": -110, "point": -(point + adj)}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 47.5},
                {"name": "Under", "price": -110, "point": 47.5}]}]}

    return [{"id": "evt1", "commence_time": KICK.isoformat(),
             "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs",
             "bookmakers": [book("draftkings", 0.0), book("fanduel", 0.5),
                            book("betmgm", -0.5)]}]


def prices(observed_at: datetime, *, american: int = -110) -> list[dict]:
    """Prices a person typed in. Nothing is fetched; no book is contacted."""
    return [
        {"canonical_game_id": GAME, "market": "SPREAD", "selection": "HOME",
         "line": -3.0, "american": american, "observed_at": observed_at,
         "user_id": "fixture-operator", "source": "fixture_price", "confirmed": True},
        {"canonical_game_id": GAME, "market": "TOTAL", "selection": "OVER",
         "line": 47.5, "american": american, "observed_at": observed_at,
         "user_id": "fixture-operator", "source": "fixture_price", "confirmed": True},
    ]


# The market point captured at each slot. A replay must present the SAME
# payload the original run saw: replaying with different quotes writes
# genuinely different observations, which looks like a duplicate-effect bug
# and is really a test feeding the handler new data.
SLOT_POINTS: dict[str, float] = {}


def payload_for_slot(slot: datetime) -> list[dict]:
    """The payload that belongs to a slot, stable across replays."""
    point = SLOT_POINTS.setdefault(slot.isoformat(), -2.5)
    return payload(slot, point=point)


def register_slot_point(slot: datetime, point: float) -> list[dict]:
    SLOT_POINTS[slot.isoformat()] = point
    return payload(slot, point=point)


class FixtureNws:
    """A deterministic stand-in for the NWS client.

    Returns the same forecast every time so the vintage is stable, and its
    presence is what tells `weather_capture` it is running against a fixture
    rather than reaching out to weather.gov. Nothing here opens a socket.
    """

    def __init__(self, *, temp_f: int = 62, wind: str = "8 mph") -> None:
        self.temp_f = temp_f
        self.wind = wind

    def resolve_grid(self, lat: float, lon: float) -> dict:
        return {"office": "BUF", "grid_x": 10, "grid_y": 20,
                "forecast_url": "fixture://forecast"}

    def fetch_forecast(self, url: str) -> tuple[dict, bytes]:
        body = {"properties": {"periods": [{
            "startTime": (KICK - timedelta(hours=1)).isoformat(),
            "endTime": (KICK + timedelta(hours=3)).isoformat(),
            "temperature": self.temp_f, "temperatureUnit": "F",
            "windSpeed": self.wind, "probabilityOfPrecipitation": {"value": 10},
            "relativeHumidity": {"value": 55},
            "shortForecast": "Partly Cloudy",
            "detailedForecast": "Partly cloudy with light wind.",
        }]}}
        raw = repr(sorted(body["properties"]["periods"][0].items())).encode()
        return body, raw


def seed_injuries(factory, *, observed_at: datetime) -> None:
    """A resolved starting quarterback and one listed player.

    Injury entry is manual by design, so the chain needs observations to
    exist before `injury_reconciliation` and `availability_computation` have
    anything to reconcile or assess.
    """
    from fde_api.forward.injuries import SourceCategory, record_injury_observation

    with factory() as s:
        for team, player, designation in (
            ("BUF", "BUF_QB_ALLEN", None),
            ("KC", "KC_QB_MAHOMES", None),
            ("BUF", "BUF_WR_DIGGS", "QUESTIONABLE"),
        ):
            record_injury_observation(
                s,
                canonical_game_id=GAME,
                team_id=team,
                player_id=player,
                report_date="2026-09-11",
                observed_at=observed_at,
                source_category=SourceCategory.OFFICIAL_VERIFIED,
                practice_status="FULL" if designation is None else "LIMITED",
                game_designation=designation,
                body_part=None if designation is None else "hamstring",
                source_reference="fixture injury report",
                data_mode=DATA_MODE,
                now=observed_at,
            )
        s.commit()


class SchedulerChain:
    """Drives one game forward through scheduled handlers only."""

    def __init__(self, factory) -> None:
        self.factory = factory
        self.clock = FrozenClock(KICK - timedelta(days=7))
        self.sched = Scheduler(
            factory, clock=self.clock,
            cohort=Cohort.FIXTURE, provider_mode=ProviderMode.FIXTURE,
            policy_version="ftp-2026-v1", data_mode=DATA_MODE,
        )
        register_all(self.sched)
        self.log: list[tuple[str, str]] = []

    def at(self, when: datetime) -> SchedulerChain:
        self.clock.set(when)
        return self

    def run(self, job: str, **params) -> dict:
        r = self.sched.run_job(job, slot=self.clock.now(), params=params or None)
        self.log.append((job, r["status"]))
        return r

    def statuses(self) -> dict[str, str]:
        return dict(self.log)

    def session(self):
        return self.factory()


# The §14 sequence, in logical order. Named so a failure says which step.
REQUIRED_SEQUENCE = (
    "schedule_refresh",
    "odds_capture",
    "consensus_build",
    "weather_capture",
    "injury_reconciliation",
    "availability_computation",
    "feature_snapshot",
    "prediction_vintage",
    "price_observation",
    "price_evaluation",
    "closing_capture",
    "result_ingestion",
    "settlement",
    "forward_evaluation",
    "data_health_reconciliation",
)


def drive(chain: SchedulerChain, *, with_injuries: bool = True) -> None:
    """The full week, in the order it actually becomes knowable.

    `seed_injuries` is False on a replay. Injury entry is manual by design,
    so seeding again genuinely appends new superseding observations - that
    is the immutable-history behaviour working, not a duplicate effect. A
    replay must exercise the SCHEDULER, so it re-runs the handlers over the
    observations that already exist.
    """
    # T-7d: the slate is known and the market has opened.
    chain.at(KICK - timedelta(days=7))
    chain.run("schedule_refresh")
    chain.run("odds_capture",
              fixture_payload=register_slot_point(chain.clock.now(), -2.5))
    chain.run("consensus_build")
    chain.run("weather_capture", nws_client=FixtureNws())
    if with_injuries:
        seed_injuries(chain.factory, observed_at=chain.clock.now())
    chain.run("injury_reconciliation")
    chain.run("availability_computation")
    chain.run("feature_snapshot")

    # T-2d: the market has moved. Recapture, then predict and price.
    chain.at(KICK - timedelta(days=2))
    chain.run("odds_capture",
              fixture_payload=register_slot_point(chain.clock.now(), -3.0))
    chain.run("consensus_build")
    chain.run("prediction_vintage")
    chain.run("price_observation",
              price_observations=prices(chain.clock.now() - timedelta(minutes=2)))
    chain.run("price_evaluation")

    # Kickoff: the close is captured without anyone seeing the outcome.
    chain.at(KICK - timedelta(minutes=5))
    chain.run("odds_capture",
              fixture_payload=register_slot_point(chain.clock.now(), -3.5))
    chain.run("consensus_build")
    chain.run("closing_capture")

    # After the whistle.
    chain.at(KICK + timedelta(hours=4))
    chain.run("result_ingestion", final_scores={GAME: (24, 20)})
    chain.run("settlement", final_scores={GAME: (24, 20)})
    chain.run("forward_evaluation")
    chain.run("data_health_reconciliation")
