"""play_by_play_{season}.parquet → per-team per-game offensive aggregates.

Only *own-offense* aggregates are stored; defensive numbers are derived
in the feature layer by looking up the opponent's offense in the same
game, which keeps a single source of truth per unit.

observed_at for every stat row is the game's result-availability instant
(kickoff + 4h30m), because box-score-derived quantities cannot be known
before the game ends. The PIT guards therefore automatically exclude a
game's own stats from its own prediction — the classic leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import polars as pl
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.canonical.team_map import canonical_team_code
from fde_api.db.models import Game, TeamGameStat

_EXPLOSIVE_RUSH_YDS = 10
_EXPLOSIVE_PASS_YDS = 15


@dataclass
class PbpLoadResult:
    season: int
    team_games_loaded: int
    plays_scanned: int


def load_pbp_team_stats(session: Session, parquet_path: Path, *, season: int) -> PbpLoadResult:
    lf = pl.scan_parquet(parquet_path)
    plays = lf.select(
        "game_id",
        "posteam",
        "season_type",
        "epa",
        "success",
        "pass",
        "rush",
        "sack",
        "interception",
        "down",
        "qtr",
        "wp",
        "yards_gained",
        "special",
        "play",
        "fixed_drive",
        "yardline_100",
        "touchdown",
    ).collect()

    real = plays.filter(
        (pl.col("posteam").is_not_null()) & (pl.col("epa").is_not_null()) & (pl.col("play") == 1)
    )

    is_dropback = (pl.col("pass") == 1) & (pl.col("special") != 1)
    is_rush = (pl.col("rush") == 1) & (pl.col("special") != 1)
    is_early_down = pl.col("down").is_in([1, 2])
    is_neutral = (pl.col("wp").is_between(0.2, 0.8)) & (pl.col("qtr") <= 3)
    is_explosive = (is_rush & (pl.col("yards_gained") >= _EXPLOSIVE_RUSH_YDS)) | (
        is_dropback & (pl.col("yards_gained") >= _EXPLOSIVE_PASS_YDS)
    )
    is_red_zone = pl.col("yardline_100") <= 20

    agg = (
        real.group_by(["game_id", "posteam"])
        .agg(
            plays=pl.len(),
            epa_per_play=pl.col("epa").mean(),
            success_rate=pl.col("success").mean(),
            dropbacks=is_dropback.sum(),
            epa_per_dropback=pl.col("epa").filter(is_dropback).mean(),
            rushes=is_rush.sum(),
            rush_epa_per_play=pl.col("epa").filter(is_rush).mean(),
            early_down_epa=pl.col("epa").filter(is_early_down).mean(),
            neutral_epa=pl.col("epa").filter(is_neutral).mean(),
            explosive_rate=is_explosive.mean(),
            sack_rate=(pl.col("sack") == 1).sum() / is_dropback.sum().clip(lower_bound=1),
            int_rate=(pl.col("interception") == 1).sum() / is_dropback.sum().clip(lower_bound=1),
            drives=pl.col("fixed_drive").n_unique(),
            red_zone_plays=is_red_zone.sum(),
            red_zone_td=(is_red_zone & (pl.col("touchdown") == 1)).sum(),
        )
        .sort(["game_id", "posteam"])
    )

    # Special-teams EPA per game from ST plays (kept separately: different denominator).
    st = (
        plays.filter((pl.col("special") == 1) & pl.col("posteam").is_not_null() & pl.col("epa").is_not_null())
        .group_by(["game_id", "posteam"])
        .agg(st_epa_total=pl.col("epa").sum(), st_plays=pl.len())
    )
    agg = agg.join(st, on=["game_id", "posteam"], how="left")

    known_games = {
        gid: (kick, obs)
        for gid, kick, obs in session.execute(
            select(Game.id, Game.kickoff_utc, Game.result_observed_at).where(Game.season == season)
        )
    }

    loaded = 0
    for row in agg.iter_rows(named=True):
        game_id = row["game_id"]
        if game_id not in known_games:
            continue  # e.g. game filtered out during games-load; DQ event already recorded there
        kickoff, observed = known_games[game_id]
        if observed is None:
            observed = kickoff + timedelta(hours=4, minutes=30) if kickoff else None
        if observed is None:
            continue
        team_id = canonical_team_code(row["posteam"])
        metrics = {
            k: (float(v) if v is not None else None)
            for k, v in row.items()
            if k not in ("game_id", "posteam")
        }
        session.merge(
            TeamGameStat(
                game_id=game_id,
                team_id=team_id,
                season=season,
                week=int(game_id.split("_")[1]),
                metrics=metrics,
                observed_at=observed,
            )
        )
        loaded += 1

    return PbpLoadResult(season=season, team_games_loaded=loaded, plays_scanned=int(real.height))
