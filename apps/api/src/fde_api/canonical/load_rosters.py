"""roster_{season}.parquet → players + seasonal roster entries.

Players are keyed by GSIS id; rows without one are recorded as data-
quality events rather than joined by display name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl
from sqlalchemy.orm import Session

from fde_api.canonical.team_map import canonical_team_code
from fde_api.db.models import DataQualityEvent, Player, RosterEntry
from fde_api.util import utc_now


@dataclass
class RosterLoadResult:
    season: int
    players_loaded: int
    entries_loaded: int
    rows_without_gsis: int


def load_rosters_parquet(session: Session, parquet_path: Path, *, season: int) -> RosterLoadResult:
    df = (
        pl.scan_parquet(parquet_path)
        .select("season", "team", "position", "status", "full_name", "gsis_id", "birth_date", "rookie_year")
        .collect()
    )
    players = entries = skipped = 0
    seen: set[tuple[str, str]] = set()
    for row in df.iter_rows(named=True):
        gsis = (row.get("gsis_id") or "").strip()
        if not gsis:
            skipped += 1
            continue
        team = canonical_team_code(row["team"])
        session.merge(
            Player(
                id=gsis,
                name=row.get("full_name") or gsis,
                position=row.get("position"),
                birth_date=str(row.get("birth_date") or "") or None,
                first_season=int(row["rookie_year"]) if row.get("rookie_year") else None,
            )
        )
        players += 1
        key = (team, gsis)
        if key in seen:
            continue
        seen.add(key)
        session.merge(
            RosterEntry(
                season=season,
                team_id=team,
                player_id=gsis,
                position=row.get("position"),
                status=row.get("status"),
                observed_at=None,  # seasonal snapshot; no per-row instant in source
            )
        )
        entries += 1

    if skipped:
        session.add(
            DataQualityEvent(
                severity="WARN",
                scope="rosters-load",
                message=f"{skipped} roster rows without GSIS id skipped (never joined by name)",
                context={"season": str(season)},
                created_at=utc_now(),
            )
        )
    return RosterLoadResult(season=season, players_loaded=players, entries_loaded=entries, rows_without_gsis=skipped)
