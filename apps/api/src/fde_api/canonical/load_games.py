"""games.csv → canonical games, teams, stadiums, officials, closing odds.

Validation failures become DataQualityEvent rows; structural corruption
(duplicate ids, unknown teams, impossible scores) fails the load loudly.

Timing model (documented limitations):
  * kickoff_utc from gameday+gametime, interpreted in US/Eastern as
    published by nflverse.
  * result_observed_at = kickoff + 4h30m — the source records no result
    publication instant, so a conservative post-game offset is used.
  * Closing-benchmark odds get observed_at = kickoff_utc and are marked
    CLOSING_BENCHMARK: usable for evaluation and residualization only,
    never as features for earlier horizons.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from fde_api.canonical.team_map import CANONICAL_TEAMS, canonical_team_code
from fde_api.db.models import (
    DataQualityEvent,
    Game,
    OddsSnapshot,
    Official,
    ProviderEntityMap,
    Stadium,
    Team,
)
from fde_api.util import utc_now

_EASTERN = ZoneInfo("America/New_York")
RESULT_AVAILABILITY_OFFSET = timedelta(hours=4, minutes=30)

MIN_AMERICAN, MAX_AMERICAN = 100, 100_000
MAX_PLAUSIBLE_SCORE = 90


@dataclass
class GamesLoadResult:
    seasons: list[int]
    games_loaded: int = 0
    games_skipped: int = 0
    odds_rows: int = 0
    quality_events: list[str] = field(default_factory=list)


def _opt_float(v: str) -> float | None:
    v = v.strip()
    if not v or v.upper() == "NA":
        return None
    return float(v)


def _opt_int(v: str) -> int | None:
    f = _opt_float(v)
    return None if f is None else int(f)


def _valid_american(price: int | None) -> bool:
    return price is None or MIN_AMERICAN <= abs(price) <= MAX_AMERICAN


def _kickoff_utc(row: dict[str, str]) -> datetime | None:
    day, tm = row.get("gameday", "").strip(), row.get("gametime", "").strip()
    if not day:
        return None
    if not tm:
        tm = "13:00"
    local = datetime.strptime(f"{day} {tm}", "%Y-%m-%d %H:%M").replace(tzinfo=_EASTERN)
    return local.astimezone(ZoneInfo("UTC"))


def load_games_csv(
    session: Session,
    payload: bytes,
    *,
    seasons: list[int],
    source_manifest_version: str,
) -> GamesLoadResult:
    result = GamesLoadResult(seasons=seasons)
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8")))

    def dq(severity: str, message: str, **context: object) -> None:
        result.quality_events.append(message)
        session.add(
            DataQualityEvent(
                severity=severity,
                scope="games-load",
                message=message,
                context={k: str(v) for k, v in context.items()},
                created_at=utc_now(),
            )
        )

    # Reference rows first (idempotent merges).
    for code, name in CANONICAL_TEAMS.items():
        session.merge(Team(id=code, name=name))

    seen_ids: set[str] = set()
    legacy_codes_used: set[str] = set()
    wanted = set(seasons)
    for row in reader:
        season = int(row["season"])
        if season not in wanted:
            continue
        game_id = row["game_id"].strip()
        if game_id in seen_ids:
            raise ValueError(f"Duplicate game_id in source: {game_id}")
        seen_ids.add(game_id)

        try:
            home = canonical_team_code(row["home_team"])
            away = canonical_team_code(row["away_team"])
        except KeyError as e:
            raise ValueError(f"Game {game_id}: unknown team ({e})") from e
        if home == away:
            raise ValueError(f"Game {game_id}: home and away are identical")

        home_score, away_score = _opt_int(row["home_score"]), _opt_int(row["away_score"])
        for label, score in (("home", home_score), ("away", away_score)):
            if score is not None and not (0 <= score <= MAX_PLAUSIBLE_SCORE):
                raise ValueError(f"Game {game_id}: impossible {label} score {score}")

        kickoff = _kickoff_utc(row)
        if kickoff is None:
            dq("WARN", f"Game {game_id} missing kickoff timestamp; excluded from replay", game_id=game_id)
            result.games_skipped += 1
            continue

        stadium_id = row.get("stadium_id", "").strip() or None
        if stadium_id:
            session.merge(
                Stadium(
                    id=stadium_id,
                    name=row.get("stadium", "").strip() or stadium_id,
                    roof=row.get("roof", "").strip() or None,
                    surface=row.get("surface", "").strip() or None,
                )
            )
        referee = row.get("referee", "").strip() or None
        referee_id = None
        if referee:
            referee_id = referee.lower().replace(" ", "-")
            session.merge(Official(id=referee_id, name=referee))

        has_result = home_score is not None and away_score is not None
        session.merge(
            Game(
                id=game_id,
                season=season,
                week=int(row["week"]),
                game_type=row.get("game_type", "REG").strip() or "REG",
                kickoff_utc=kickoff,
                home_team_id=home,
                away_team_id=away,
                home_score=home_score,
                away_score=away_score,
                overtime=(_opt_int(row.get("overtime", "")) or 0) > 0 if has_result else None,
                stadium_id=stadium_id,
                roof=row.get("roof", "").strip() or None,
                surface=row.get("surface", "").strip() or None,
                temp_f=_opt_float(row.get("temp", "")),
                wind_mph=_opt_float(row.get("wind", "")),
                referee_id=referee_id,
                home_rest_days=_opt_int(row.get("home_rest", "")),
                away_rest_days=_opt_int(row.get("away_rest", "")),
                div_game=(_opt_int(row.get("div_game", "")) or 0) > 0,
                home_qb_id=row.get("home_qb_id", "").strip() or None,
                away_qb_id=row.get("away_qb_id", "").strip() or None,
                home_coach=row.get("home_coach", "").strip() or None,
                away_coach=row.get("away_coach", "").strip() or None,
                result_observed_at=(kickoff + RESULT_AVAILABILITY_OFFSET) if has_result else None,
                source_manifest_version=source_manifest_version,
            )
        )
        result.games_loaded += 1

        # Note era codes for the explicit id-based mapping table (inserted once below).
        for provider_code in (row["home_team"], row["away_team"]):
            code = provider_code.strip().upper()
            if canonical_team_code(code) != code:
                legacy_codes_used.add(code)

        result.odds_rows += _load_closing_odds(session, row, game_id, kickoff, source_manifest_version, dq)

    _upsert_team_mappings(session, legacy_codes_used)
    return result


def _upsert_team_mappings(session: Session, legacy_codes: set[str]) -> None:
    from sqlalchemy import select

    existing = set(
        session.execute(
            select(ProviderEntityMap.provider_id).where(
                ProviderEntityMap.provider == "nflverse", ProviderEntityMap.entity_type == "team"
            )
        ).scalars()
    )
    for code in sorted(legacy_codes - existing):
        session.add(
            ProviderEntityMap(
                provider="nflverse",
                entity_type="team",
                provider_id=code,
                canonical_id=canonical_team_code(code),
            )
        )


def _load_closing_odds(
    session: Session,
    row: dict[str, str],
    game_id: str,
    kickoff: datetime,
    manifest_version: str,
    dq,
) -> int:
    """Store the source's closing market columns as CLOSING_BENCHMARK
    snapshots. nflverse spread_line is home expected margin (positive =
    home favored); the bookmaker-style home handicap is its negation."""
    rows = 0
    spread_line = _opt_float(row.get("spread_line", ""))
    home_spread_odds = _opt_int(row.get("home_spread_odds", ""))
    away_spread_odds = _opt_int(row.get("away_spread_odds", ""))
    total_line = _opt_float(row.get("total_line", ""))
    over_odds = _opt_int(row.get("over_odds", ""))
    under_odds = _opt_int(row.get("under_odds", ""))
    home_ml = _opt_int(row.get("home_moneyline", ""))
    away_ml = _opt_int(row.get("away_moneyline", ""))

    for price, label in (
        (home_spread_odds, "home_spread_odds"),
        (away_spread_odds, "away_spread_odds"),
        (over_odds, "over_odds"),
        (under_odds, "under_odds"),
        (home_ml, "home_moneyline"),
        (away_ml, "away_moneyline"),
    ):
        if not _valid_american(price):
            dq("WARN", f"Game {game_id}: invalid american price {price} in {label}; market row dropped")
            return 0

    def add(market: str, **kw: object) -> None:
        nonlocal rows
        session.add(
            OddsSnapshot(
                game_id=game_id,
                book="nflverse-close",
                market=market,
                snapshot_kind="CLOSING_BENCHMARK",
                observed_at=kickoff,
                source_manifest_version=manifest_version,
                **kw,  # type: ignore[arg-type]
            )
        )
        rows += 1

    if spread_line is not None:
        add(
            "SPREAD",
            line=-spread_line,
            home_price_american=home_spread_odds,
            away_price_american=away_spread_odds,
        )
    if total_line is not None:
        add("TOTAL", line=total_line, over_price_american=over_odds, under_price_american=under_odds)
    if home_ml is not None and away_ml is not None:
        add("MONEYLINE", home_price_american=home_ml, away_price_american=away_ml)
    return rows
