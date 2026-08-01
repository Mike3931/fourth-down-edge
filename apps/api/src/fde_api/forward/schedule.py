"""Real NFL schedule ingestion with full observation history.

A schedule is not a fact; it is a sequence of observations. Kickoff times
move, games get flexed, and occasionally a game is postponed or
cancelled. This module records each state it observes as a NEW row. The
first observation of a game is never overwritten, so "what did we believe
the schedule was on the day we made this prediction" is always
answerable.

Revision detection is by content hash over the fields that matter
(kickoff, teams, venue, status, neutral/international flags). An
unchanged re-poll writes nothing.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.canonical.team_map import canonical_team_code
from fde_api.db.forward_models import ScheduleObservation
from fde_api.forward.modes import DataMode
from fde_api.forward.venues import INTERNATIONAL_COUNTRIES, resolve_venue
from fde_api.util import utc_now

_EASTERN = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

SEASON_TYPE_MAP = {
    "PRE": "PRE",
    "REG": "REG",
    "WC": "POST",
    "DIV": "POST",
    "CON": "POST",
    "SB": "POST",
}


@dataclass
class ScheduleIngestResult:
    season: int
    observed_games: int = 0
    new_games: int = 0
    revisions: int = 0
    unchanged: int = 0
    postponed: int = 0
    cancelled: int = 0
    unmapped_stadiums: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "observed_games": self.observed_games,
            "new_games": self.new_games,
            "revisions": self.revisions,
            "unchanged": self.unchanged,
            "postponed": self.postponed,
            "cancelled": self.cancelled,
            "unmapped_stadiums": sorted(self.unmapped_stadiums),
            "warnings": self.warnings,
        }


def _kickoff_utc(gameday: str, gametime: str) -> datetime | None:
    gameday, gametime = gameday.strip(), gametime.strip()
    if not gameday:
        return None
    if not gametime:
        gametime = "13:00"
    local = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M").replace(tzinfo=_EASTERN)
    return local.astimezone(_UTC)


def _content_hash(fields: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


def _describe_change(prev: ScheduleObservation, new_fields: dict[str, Any]) -> str:
    changes = []
    if prev.kickoff_utc != new_fields["kickoff_utc"]:
        changes.append(f"kickoff {prev.kickoff_utc} -> {new_fields['kickoff_utc']}")
    if prev.game_status != new_fields["game_status"]:
        changes.append(f"status {prev.game_status} -> {new_fields['game_status']}")
    if prev.stadium_id != new_fields["stadium_id"]:
        changes.append(f"venue {prev.stadium_id} -> {new_fields['stadium_id']}")
    if prev.week != new_fields["week"]:
        changes.append(f"week {prev.week} -> {new_fields['week']}")
    if prev.neutral_site != new_fields["neutral_site"]:
        changes.append(f"neutral_site {prev.neutral_site} -> {new_fields['neutral_site']}")
    return "; ".join(changes) or "non-material field change"


def ingest_schedule(
    session: Session,
    payload: bytes,
    *,
    season: int,
    provider: str = "nflverse",
    source_manifest_version: str | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    observed_at: datetime | None = None,
) -> ScheduleIngestResult:
    """Record the current observed schedule state for one season.

    Existing observations are never modified. When a game's material
    fields differ from its latest observation, a new row is appended with
    `supersedes_id` pointing at the prior one and a human-readable
    `change_summary`.
    """
    observed_at = observed_at or utc_now()
    result = ScheduleIngestResult(season=season)
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))

    latest: dict[str, ScheduleObservation] = {}
    for obs in session.scalars(
        select(ScheduleObservation)
        .where(
            ScheduleObservation.season == season,
            ScheduleObservation.data_mode == data_mode.value,
            ScheduleObservation.supersedes_id.is_(None) | ScheduleObservation.supersedes_id.isnot(None),
        )
        .order_by(ScheduleObservation.observed_at)
    ):
        latest[obs.canonical_game_id] = obs  # last write wins => newest

    for row in reader:
        if int(row["season"]) != season:
            continue
        result.observed_games += 1
        game_id = row["game_id"].strip()

        raw_type = (row.get("game_type") or "REG").strip().upper()
        season_type = SEASON_TYPE_MAP.get(raw_type, "REG")

        try:
            home = canonical_team_code(row["home_team"])
            away = canonical_team_code(row["away_team"])
        except KeyError as e:
            result.warnings.append(f"{game_id}: unknown team ({e}); observation skipped")
            continue

        kickoff = _kickoff_utc(row.get("gameday", ""), row.get("gametime", ""))
        raw_stadium_id = (row.get("stadium_id") or "").strip() or None
        stadium_name = (row.get("stadium") or "").strip() or None
        location = (row.get("location") or "Home").strip().lower()
        neutral = location == "neutral"

        # For neutral games the source's stadium_id/roof describe the home
        # club's stadium, not the actual site; resolve_venue ignores them.
        venue = resolve_venue(stadium_id=raw_stadium_id, stadium_name=stadium_name, neutral_site=neutral)
        if venue is None and (raw_stadium_id or neutral):
            result.unmapped_stadiums.add(str(stadium_name or raw_stadium_id or "?"))
        # Store the RESOLVED venue id so downstream weather/roof logic can
        # never pick up the wrong stadium for an international game.
        stadium_id = venue.id if venue is not None else (None if neutral else raw_stadium_id)
        international = bool(venue and venue.country in INTERNATIONAL_COUNTRIES)

        # The source has no explicit status column; a scheduled game with no
        # kickoff is treated as unresolved rather than assumed playable.
        game_status = "SCHEDULED" if kickoff else "UNRESOLVED"

        fields: dict[str, Any] = {
            "season_type": season_type,
            "week": int(row["week"]),
            "home_team_id": home,
            "away_team_id": away,
            "kickoff_utc": kickoff,
            "venue_timezone": venue.tz if venue else None,
            "stadium_id": stadium_id,
            "stadium_name": stadium_name,
            "neutral_site": neutral,
            "international": international,
            "game_status": game_status,
        }
        chash = _content_hash(fields)

        prev = latest.get(game_id)
        if prev is not None and prev.content_hash == chash:
            result.unchanged += 1
            continue

        obs = ScheduleObservation(
            data_mode=data_mode.value,
            canonical_game_id=game_id,
            provider=provider,
            provider_game_id=game_id,
            season=season,
            content_hash=chash,
            source_manifest_version=source_manifest_version,
            observed_at=observed_at,
            supersedes_id=prev.id if prev is not None else None,
            change_summary=_describe_change(prev, fields) if prev is not None else None,
            **fields,
        )
        session.add(obs)
        session.flush()
        latest[game_id] = obs

        if prev is None:
            result.new_games += 1
        else:
            result.revisions += 1
        if game_status == "POSTPONED":
            result.postponed += 1
        elif game_status == "CANCELLED":
            result.cancelled += 1

    return result


def record_status_change(
    session: Session,
    *,
    canonical_game_id: str,
    new_status: str,
    reason: str,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    observed_at: datetime | None = None,
    new_kickoff_utc: datetime | None = None,
) -> ScheduleObservation:
    """Append a postponement / cancellation / kickoff change.

    Used when the status change is known from an operator or a source that
    does not express it as a schedule row. Never mutates the prior state.
    """
    if new_status not in ("SCHEDULED", "POSTPONED", "CANCELLED", "UNRESOLVED"):
        raise ValueError(f"Unsupported game status {new_status!r}")
    prev = current_schedule_state(session, canonical_game_id, data_mode)
    if prev is None:
        raise LookupError(f"No prior schedule observation for {canonical_game_id}")

    fields: dict[str, Any] = {
        "season_type": prev.season_type,
        "week": prev.week,
        "home_team_id": prev.home_team_id,
        "away_team_id": prev.away_team_id,
        "kickoff_utc": new_kickoff_utc if new_kickoff_utc is not None else prev.kickoff_utc,
        "venue_timezone": prev.venue_timezone,
        "stadium_id": prev.stadium_id,
        "stadium_name": prev.stadium_name,
        "neutral_site": prev.neutral_site,
        "international": prev.international,
        "game_status": new_status,
    }
    obs = ScheduleObservation(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        provider=prev.provider,
        provider_game_id=prev.provider_game_id,
        season=prev.season,
        content_hash=_content_hash(fields),
        source_manifest_version=None,
        observed_at=observed_at or utc_now(),
        supersedes_id=prev.id,
        change_summary=f"{_describe_change(prev, fields)} ({reason})",
        **fields,
    )
    session.add(obs)
    session.flush()
    return obs


def current_schedule_state(
    session: Session, canonical_game_id: str, data_mode: DataMode = DataMode.LIVE_RESEARCH
) -> ScheduleObservation | None:
    """The most recent observation for a game."""
    return session.scalars(
        select(ScheduleObservation)
        .where(
            ScheduleObservation.canonical_game_id == canonical_game_id,
            ScheduleObservation.data_mode == data_mode.value,
        )
        .order_by(ScheduleObservation.observed_at.desc(), ScheduleObservation.id.desc())
        .limit(1)
    ).first()


def schedule_state_as_of(
    session: Session,
    canonical_game_id: str,
    as_of_at: datetime,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> ScheduleObservation | None:
    """What we believed the schedule was at a past instant — the
    point-in-time-correct view used when building feature snapshots."""
    return session.scalars(
        select(ScheduleObservation)
        .where(
            ScheduleObservation.canonical_game_id == canonical_game_id,
            ScheduleObservation.data_mode == data_mode.value,
            ScheduleObservation.observed_at <= as_of_at,
        )
        .order_by(ScheduleObservation.observed_at.desc(), ScheduleObservation.id.desc())
        .limit(1)
    ).first()


def current_slate(
    session: Session,
    season: int,
    *,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    week: int | None = None,
    season_types: tuple[str, ...] = ("REG", "POST"),
) -> list[ScheduleObservation]:
    """Latest observation per game for a season, newest state only."""
    stmt = select(ScheduleObservation).where(
        ScheduleObservation.season == season,
        ScheduleObservation.data_mode == data_mode.value,
    )
    if week is not None:
        stmt = stmt.where(ScheduleObservation.week == week)
    by_game: dict[str, ScheduleObservation] = {}
    for obs in session.scalars(stmt.order_by(ScheduleObservation.observed_at, ScheduleObservation.id)):
        by_game[obs.canonical_game_id] = obs
    return sorted(
        (o for o in by_game.values() if o.season_type in season_types),
        key=lambda o: (o.kickoff_utc or datetime.max.replace(tzinfo=_UTC), o.canonical_game_id),
    )


def schedule_history(
    session: Session, canonical_game_id: str, data_mode: DataMode = DataMode.LIVE_RESEARCH
) -> list[ScheduleObservation]:
    """Full revision chain for a game, oldest first — the audit trail."""
    return list(
        session.scalars(
            select(ScheduleObservation)
            .where(
                ScheduleObservation.canonical_game_id == canonical_game_id,
                ScheduleObservation.data_mode == data_mode.value,
            )
            .order_by(ScheduleObservation.observed_at, ScheduleObservation.id)
        )
    )


def last_schedule_refresh(
    session: Session, season: int, data_mode: DataMode = DataMode.LIVE_RESEARCH
) -> datetime | None:
    return session.scalar(
        select(ScheduleObservation.observed_at)
        .where(
            ScheduleObservation.season == season,
            ScheduleObservation.data_mode == data_mode.value,
        )
        .order_by(ScheduleObservation.observed_at.desc())
        .limit(1)
    )
