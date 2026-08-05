"""One immutable manifest, consumed by both execution paths.

Parity failed for a boring reason: the two chains were driven by two
hand-written sequences that had drifted apart. The scheduler captured odds
at three slots and generated every horizon whose cutoff had passed; the
direct chain generated one. Both were "correct" and they disagreed, which
is exactly the failure mode a parity gate exists to catch — except the
disagreement was in the fixtures, not the system.

Two hand-maintained fixtures will always drift. So there is one manifest,
it is frozen, it carries a content hash, and both paths read it. A change
to what either path does is a change to this file, visible as a changed
hash.

The manifest describes WHAT HAPPENS IN THE WORLD — when quotes appeared,
when the report was published, what price the operator saw. It does not
describe how either path should execute; that stays with each driver. The
line between those two is what keeps this a scenario rather than a script.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

MANIFEST_VERSION = "scenario-manifest-v1"

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)


@dataclass(frozen=True)
class OddsSlot:
    """One provider poll: when it happened and what the market said."""

    at: datetime
    spread_point: float
    total_point: float = 47.5
    price_american: int = -110


@dataclass(frozen=True)
class InjuryVintage:
    """One published injury report line."""

    at: datetime
    team_id: str
    player_id: str
    practice_status: str
    game_designation: str | None
    body_part: str | None
    report_date: str = "2026-09-11"
    source_category: str = "OFFICIAL_VERIFIED"


@dataclass(frozen=True)
class WeatherVintage:
    at: datetime
    temp_f: int = 62
    wind: str = "8 mph"


@dataclass(frozen=True)
class PriceObservation:
    """A price a person saw and typed in. Nothing is fetched."""

    observed_at: datetime
    market: str
    selection: str
    line: float | None
    american: int
    user_id: str = "fixture-operator"
    source: str = "fixture_price"
    confirmed: bool = True


@dataclass(frozen=True)
class ScenarioManifest:
    """The complete, frozen description of one game's week."""

    canonical_game_id: str
    kickoff_utc: datetime
    stadium_id: str
    season: int
    week: int
    home_team_id: str
    away_team_id: str
    home_qb_id: str
    away_qb_id: str

    cohort: str
    data_mode: str
    provider_mode: str
    policy_version: str

    odds_slots: tuple[OddsSlot, ...]
    weather_vintages: tuple[WeatherVintage, ...]
    injury_vintages: tuple[InjuryVintage, ...]

    # The cutoff at which availability is assessed. A single value because
    # both paths must assess at the SAME instant - assessing at different
    # cutoffs produced different assessments and a parity failure that
    # looked like a bug in the assessor.
    availability_cutoff: datetime

    # Horizons the scheduler will generate, given the moment it runs
    # prediction_vintage. Listed explicitly rather than derived, so a change
    # to the horizon table shows up here as a changed hash instead of
    # silently altering what parity compares.
    prediction_slot: datetime
    prediction_horizons: tuple[str, ...]

    price_observations: tuple[PriceObservation, ...]
    price_evaluation_slot: datetime

    closing_slot: datetime
    closing_rule_version: str

    result_observed_at: datetime
    home_score: int
    away_score: int

    # Model moments, supplied rather than computed so both paths see
    # identical model output. The property under test is the CHAIN; letting
    # the model vary between paths would make a parity failure ambiguous.
    moments: tuple[float, float, float, float]

    schedule_observed_at: datetime
    notes: str = ""
    version: str = MANIFEST_VERSION
    _hash: str = field(default="", repr=False, compare=False)

    # ---- rendering ---------------------------------------------------- #

    def as_payload(self) -> dict[str, Any]:
        """Canonical dict, with timestamps as UTC ISO strings."""

        def render(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.astimezone(UTC).isoformat()
            if isinstance(value, tuple | list):
                return [render(v) for v in value]
            if isinstance(value, dict):
                return {k: render(v) for k, v in value.items() if k != "_hash"}
            return value

        return {k: render(v) for k, v in asdict(self).items() if k != "_hash"}

    @property
    def content_hash(self) -> str:
        """A hash of the scenario itself.

        Both paths assert they consumed the same one. Two drivers reading
        two manifests is the drift this file exists to prevent, and a hash
        is how that becomes checkable rather than assumed.
        """
        canonical = json.dumps(self.as_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---- convenience -------------------------------------------------- #

    @property
    def odds_slot_times(self) -> tuple[datetime, ...]:
        return tuple(s.at for s in self.odds_slots)

    @property
    def consensus_cutoffs(self) -> tuple[datetime, ...]:
        """A consensus is built at every odds slot. Same rule for both
        paths, derived from one place."""
        return self.odds_slot_times

    def payload_for(self, slot: OddsSlot) -> list[dict[str, Any]]:
        """The provider payload for one slot, in provider shape."""

        def book(key: str, adj: float) -> dict[str, Any]:
            return {"key": key, "last_update": slot.at.isoformat(), "markets": [
                {"key": "spreads", "outcomes": [
                    {"name": "Buffalo Bills", "price": slot.price_american,
                     "point": slot.spread_point + adj},
                    {"name": "Kansas City Chiefs", "price": slot.price_american,
                     "point": -(slot.spread_point + adj)}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": slot.price_american,
                     "point": slot.total_point},
                    {"name": "Under", "price": slot.price_american,
                     "point": slot.total_point}]}]}

        return [{
            "id": "evt1",
            "commence_time": self.kickoff_utc.isoformat(),
            "home_team": "Buffalo Bills",
            "away_team": "Kansas City Chiefs",
            "bookmakers": [book("draftkings", 0.0), book("fanduel", 0.5),
                           book("betmgm", -0.5)],
        }]


# --------------------------------------------------------------------------- #
# The canonical scenario
# --------------------------------------------------------------------------- #

_T7 = KICK - timedelta(days=7)
_T2 = KICK - timedelta(days=2)
_TCLOSE = KICK - timedelta(minutes=5)
_TPOST = KICK + timedelta(hours=4)

COMPLETE_GAME = ScenarioManifest(
    canonical_game_id="2026_02_KC_BUF",
    kickoff_utc=KICK,
    stadium_id="BUF00",
    season=2026,
    week=2,
    home_team_id="BUF",
    away_team_id="KC",
    home_qb_id="BUF_QB_ALLEN",
    away_qb_id="KC_QB_MAHOMES",
    cohort="fixture",
    data_mode="DEMO",
    provider_mode="FIXTURE",
    policy_version="ftp-2026-v1",
    schedule_observed_at=KICK - timedelta(days=30),
    odds_slots=(
        OddsSlot(at=_T7, spread_point=-2.5),
        OddsSlot(at=_T2, spread_point=-3.0),
        OddsSlot(at=_TCLOSE, spread_point=-3.5),
    ),
    weather_vintages=(WeatherVintage(at=_T7),),
    injury_vintages=(
        InjuryVintage(at=_T7, team_id="BUF", player_id="BUF_QB_ALLEN",
                      practice_status="FULL", game_designation=None, body_part=None),
        InjuryVintage(at=_T7, team_id="KC", player_id="KC_QB_MAHOMES",
                      practice_status="FULL", game_designation=None, body_part=None),
        InjuryVintage(at=_T7, team_id="BUF", player_id="BUF_WR_DIGGS",
                      practice_status="LIMITED", game_designation="QUESTIONABLE",
                      body_part="hamstring"),
    ),
    # The scheduler assesses availability at T-7d, when it runs
    # availability_computation. The direct chain must use the same instant.
    availability_cutoff=_T7,
    prediction_slot=_T2,
    # At T-2d these three cutoffs have passed. FINAL_INJURY_REPORT (T-1d)
    # and PREGAME (T-90m) have not, so the scheduler does not generate them
    # and neither does the direct chain.
    prediction_horizons=("OPENING", "EARLY_WEEK", "PRACTICE_UPDATE"),
    price_observations=(
        # +150 on the spread. Generous enough that the FROZEN rules reach
        # RESEARCH_CANDIDATE on their own, so the chain exercises a real
        # simulated fill and a non-null CLV. The threshold, the uncertainty
        # haircut and the watch band are untouched: forcing a fill by moving
        # a threshold would prove only that the threshold can be moved.
        PriceObservation(observed_at=_T2 - timedelta(minutes=2), market="SPREAD",
                         selection="HOME", line=-3.0, american=150),
        PriceObservation(observed_at=_T2 - timedelta(minutes=2), market="TOTAL",
                         selection="OVER", line=47.5, american=-110),
    ),
    price_evaluation_slot=_T2,
    closing_slot=_TCLOSE,
    closing_rule_version="close-v1",
    result_observed_at=_TPOST,
    home_score=24,
    away_score=20,
    moments=(3.2, 13.5, 47.0, 10.5),
    notes=(
        "A deterministic US outdoor game at a governed venue. Fixture cohort "
        "only: no observation here may enter burn_in or official_forward_test."
    ),
)


SCHEDULE_HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def schedule_csv(manifest: ScenarioManifest = COMPLETE_GAME) -> bytes:
    """The provider schedule row for this scenario."""
    base = dict.fromkeys(SCHEDULE_HEADER.split(","), "")
    base.update({
        "game_id": manifest.canonical_game_id,
        "season": str(manifest.season),
        "game_type": "REG",
        "week": str(manifest.week),
        "gameday": manifest.kickoff_utc.date().isoformat(),
        "gametime": "13:00",
        "away_team": manifest.away_team_id,
        "home_team": manifest.home_team_id,
        "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
        "stadium_id": manifest.stadium_id, "stadium": "Highmark Stadium",
        "away_rest": "7", "home_rest": "7",
        "home_qb_name": "Josh Allen", "away_qb_name": "Patrick Mahomes",
    })
    row = ",".join(base[k] for k in SCHEDULE_HEADER.split(","))
    return ("\n".join([SCHEDULE_HEADER, row]) + "\n").encode()


class FixtureNws:
    """A deterministic stand-in for the NWS client.

    Its presence is what tells `weather_capture` it is running against a
    fixture rather than reaching weather.gov. Nothing here opens a socket.
    """

    def __init__(self, vintage: WeatherVintage | None = None) -> None:
        self.vintage = vintage or COMPLETE_GAME.weather_vintages[0]

    def resolve_grid(self, lat: float, lon: float) -> dict[str, Any]:
        return {"office": "BUF", "grid_x": 10, "grid_y": 20,
                "forecast_url": "fixture://forecast"}

    def fetch_forecast(self, url: str) -> tuple[dict[str, Any], bytes]:
        body = {"properties": {"periods": [{
            "startTime": (KICK - timedelta(hours=1)).isoformat(),
            "endTime": (KICK + timedelta(hours=3)).isoformat(),
            "temperature": self.vintage.temp_f, "temperatureUnit": "F",
            "windSpeed": self.vintage.wind,
            "probabilityOfPrecipitation": {"value": 10},
            "relativeHumidity": {"value": 55},
            "shortForecast": "Partly Cloudy",
            "detailedForecast": "Partly cloudy with light wind.",
        }]}}
        raw = repr(sorted(body["properties"]["periods"][0].items())).encode()
        return body, raw
