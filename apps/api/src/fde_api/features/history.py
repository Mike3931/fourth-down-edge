"""In-memory league history for fast point-in-time feature computation.

Loads every game + team-game stat once, then answers "what did we know
about team T at instant X" with vectorized filters. All access routes
through observed_at <= as_of_at; nothing here reads a game's own row
when building features for that game.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.models import Game, TeamGameStat
from fde_api.pit.guards import LookaheadError

# Metric keys pulled from TeamGameStat.metrics into fast columns.
STAT_KEYS = (
    "epa_per_play",
    "epa_per_dropback",
    "rush_epa_per_play",
    "success_rate",
    "early_down_epa",
    "neutral_epa",
    "explosive_rate",
    "sack_rate",
    "int_rate",
    "dropbacks",
    "drives",
    "plays",
    "red_zone_plays",
    "red_zone_td",
    "st_epa_total",
    "st_plays",
)


@dataclass(frozen=True)
class TeamGameRow:
    game_id: str
    season: int
    week: int
    team: str
    opponent: str
    is_home: bool
    kickoff: datetime
    observed_at: datetime  # result-availability instant
    stats: dict[str, float | None]


@dataclass(frozen=True)
class GameRow:
    id: str
    season: int
    week: int
    game_type: str
    kickoff: datetime
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    result_observed_at: datetime | None
    roof: str | None
    surface: str | None
    home_rest: int | None
    away_rest: int | None
    div_game: bool | None
    home_qb_id: str | None
    away_qb_id: str | None
    home_coach: str | None
    away_coach: str | None
    referee_id: str | None
    stadium_id: str | None


class LeagueHistory:
    def __init__(self, games: list[GameRow], team_games: list[TeamGameRow]) -> None:
        self.games = sorted(games, key=lambda g: g.kickoff)
        self.by_id = {g.id: g for g in self.games}
        self._team_rows: dict[str, list[TeamGameRow]] = {}
        for row in sorted(team_games, key=lambda r: r.kickoff):
            self._team_rows.setdefault(row.team, []).append(row)

    @classmethod
    def load(cls, session: Session) -> LeagueHistory:
        games: list[GameRow] = []
        for g in session.scalars(select(Game)):
            if g.kickoff_utc is None:
                continue
            games.append(
                GameRow(
                    id=g.id,
                    season=g.season,
                    week=g.week,
                    game_type=g.game_type,
                    kickoff=_aware(g.kickoff_utc),
                    home=g.home_team_id,
                    away=g.away_team_id,
                    home_score=g.home_score,
                    away_score=g.away_score,
                    result_observed_at=_aware_opt(g.result_observed_at),
                    roof=g.roof,
                    surface=g.surface,
                    home_rest=g.home_rest_days,
                    away_rest=g.away_rest_days,
                    div_game=g.div_game,
                    home_qb_id=g.home_qb_id,
                    away_qb_id=g.away_qb_id,
                    home_coach=g.home_coach,
                    away_coach=g.away_coach,
                    referee_id=g.referee_id,
                    stadium_id=g.stadium_id,
                )
            )
        by_id = {g.id: g for g in games}
        team_games: list[TeamGameRow] = []
        for s in session.scalars(select(TeamGameStat)):
            grow = by_id.get(s.game_id)
            if grow is None or s.observed_at is None:
                continue
            opponent = grow.away if s.team_id == grow.home else grow.home
            team_games.append(
                TeamGameRow(
                    game_id=s.game_id,
                    season=s.season,
                    week=s.week,
                    team=s.team_id,
                    opponent=opponent,
                    is_home=s.team_id == grow.home,
                    kickoff=grow.kickoff,
                    observed_at=_aware(s.observed_at),
                    stats={k: s.metrics.get(k) for k in STAT_KEYS},
                )
            )
        return cls(games, team_games)

    def rows_for(self, team: str, as_of_at: datetime, exclude_game_id: str | None = None) -> list[TeamGameRow]:
        """All completed team-games known at the cutoff, oldest→newest.
        A game's own row can never enter its features: rows are keyed by
        observed_at (post-final), and exclude_game_id belts-and-braces it."""
        if as_of_at.tzinfo is None:
            raise LookaheadError("as_of_at must be timezone-aware")
        return [
            r
            for r in self._team_rows.get(team, [])
            if r.observed_at <= as_of_at and r.game_id != exclude_game_id
        ]

    def all_rows_at(self, as_of_at: datetime) -> list[TeamGameRow]:
        return [r for rows in self._team_rows.values() for r in rows if r.observed_at <= as_of_at]

    def teams(self) -> list[str]:
        return sorted(self._team_rows)


def _aware(ts: datetime) -> datetime:
    from datetime import UTC

    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _aware_opt(ts: datetime | None) -> datetime | None:
    return None if ts is None else _aware(ts)
