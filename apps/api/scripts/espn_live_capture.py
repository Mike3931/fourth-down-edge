"""Capture tonight's real slate and real market prices from ESPN's public API.

WHY THIS SOURCE EXISTS ALONGSIDE THE ODDS API
    The configured schedule feed (nflverse `games.csv`) has never carried a
    preseason game — 1999 through 2026, every season, only REG/WC/DIV/CON/SB
    and zero July or August fixtures. And the configured odds adapter needs
    FDE_ODDS_API_KEY. So with no key and a preseason kickoff, the platform
    is blind to a game that is genuinely happening.

    ESPN's public scoreboard API needs no credential and carries both the
    fixture and sportsbook lines. This captures from it.

WHAT IT IS AND IS NOT
    It IS live market data: real prices, real books, real timestamps.
    It is NOT The Odds API, and every row it writes says `provider="espn"`
    so the two can never be confused in the record.

    bet365 is not contacted, scraped, or inspected. Nothing here implies a
    price is currently available to transact.

WHAT IT WILL NOT DO
    Manufacture a consensus. Consensus requires a minimum number of
    independent books, and ESPN typically exposes one. One book is a quote,
    not a consensus, and the honest output of a one-book slate is
    DATA_INCOMPLETE. This script writes quotes and lets the consensus
    builder reach its own verdict rather than lowering the bar to produce a
    number that would look like agreement among books that were never
    consulted.

Run:
  python scripts/espn_live_capture.py --dry-run
  python scripts/espn_live_capture.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
PROVIDER = "espn"
SOURCE_VERSION = "espn-public-scoreboard-v1"

# ESPN season types. 1 = preseason, 2 = regular, 3 = postseason.
_SEASON_TYPE = {1: "PRE", 2: "REG", 3: "POST"}

# ESPN status -> the schedule vocabulary, which is deliberately narrow:
# SCHEDULED / POSTPONED / CANCELLED / UNRESOLVED.
#
# FINAL is absent on purpose. In this system FINAL means "a result has been
# recorded", and the score lives in that observation's change summary. A
# schedule capture that stamped FINAL created an observation claiming the
# game had finished while carrying no score, so the real score arriving
# afterwards was rejected as a correction to a final that already existed.
# The guard was right; the status was the lie. `ingest_result` promotes the
# game to FINAL, with the score, and nothing else may.
#
# An unrecognised status maps to UNRESOLVED rather than SCHEDULED, because
# the safe default is "we do not know" rather than "it has not happened".
_GAME_STATUS = {
    "STATUS_SCHEDULED": "SCHEDULED",
    "STATUS_IN_PROGRESS": "SCHEDULED",
    "STATUS_HALFTIME": "SCHEDULED",
    "STATUS_END_PERIOD": "SCHEDULED",
    "STATUS_FINAL": "SCHEDULED",
    "STATUS_POSTPONED": "POSTPONED",
    "STATUS_CANCELED": "CANCELLED",
    "STATUS_SUSPENDED": "POSTPONED",
}


def _american_to_decimal(american: int) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def _parse_american(text: Any) -> int | None:
    """ESPN renders prices as '+100' / '-110' / 'EVEN'."""
    if text is None:
        return None
    s = str(text).strip().upper().replace("EVEN", "+100")
    try:
        return int(s.replace("+", "")) if s.startswith(("+", "-")) else int(s)
    except ValueError:
        return None


def fetch(dates: str | None = None) -> dict[str, Any]:
    url = SCOREBOARD + (f"?dates={dates}" if dates else "")
    r = httpx.get(url, timeout=45, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def _spread_from_flags(odds: dict[str, Any], side: str) -> float | None:
    """A signed spread for `side`, or None if the sign cannot be established.

    The per-side `pointSpread` block is the source of truth. When it is
    missing, the only other statement of the handicap is the scalar
    `spread`, which is unsigned with respect to a side - and inferring the
    sign from position got it exactly backwards, recording the favourite as
    the underdog. Nothing about that is visible afterwards: an inverted
    line is a perfectly plausible number.

    So the sign comes from the provider's explicit favourite/underdog
    flags, and when those are absent nothing is emitted. That matches how
    prices are already handled here: a missing one is left out rather than
    defaulted, because an invented value is indistinguishable from a real
    one once it is in the table.
    """
    raw = odds.get("spread")
    if raw is None:
        return None
    try:
        magnitude = abs(float(raw))
    except (TypeError, ValueError):
        return None

    block = odds.get(f"{side}TeamOdds") or {}
    if block.get("favorite") is True:
        return -magnitude
    if block.get("underdog") is True:
        return magnitude
    # The other side may say it unambiguously instead.
    other = odds.get("awayTeamOdds" if side == "home" else "homeTeamOdds") or {}
    if other.get("favorite") is True:
        return magnitude
    if other.get("underdog") is True:
        return -magnitude
    return None


def _quotes_from_odds(odds: dict[str, Any], *, home: str, away: str) -> list[dict[str, Any]]:
    """Turn one book's block into canonical quote rows.

    Only prices the book actually published are emitted. A missing price is
    left out rather than defaulted to -110: an invented price is
    indistinguishable from a real one once it is in the table.
    """
    book = (odds.get("provider") or {}).get("name", "unknown")
    book_key = book.lower().replace(" ", "")
    out: list[dict[str, Any]] = []

    ps = odds.get("pointSpread") or {}
    for side in ("home", "away"):
        blk = (ps.get(side) or {}).get("close") or {}
        american = _parse_american(blk.get("odds"))
        line_txt = str(blk.get("line") or "").replace("+", "")
        try:
            line = float(line_txt) if line_txt else _spread_from_flags(odds, side)
        except ValueError:
            line = None
        if american is not None and line is not None:
            out.append({"sportsbook": book_key, "market": "SPREAD",
                        "selection": side.upper(), "line": line, "american": american})

    ml = odds.get("moneyline") or {}
    for side in ("home", "away"):
        american = _parse_american(((ml.get(side) or {}).get("close") or {}).get("odds"))
        if american is not None:
            out.append({"sportsbook": book_key, "market": "MONEYLINE",
                        "selection": side.upper(), "line": None, "american": american})

    total = odds.get("total") or {}
    ou = odds.get("overUnder")
    for side, sel in (("over", "OVER"), ("under", "UNDER")):
        blk = (total.get(side) or {}).get("close") or {}
        american = _parse_american(blk.get("odds"))
        raw = str(blk.get("line") or "").lstrip("ou")
        try:
            line = float(raw) if raw else (float(ou) if ou is not None else None)
        except ValueError:
            line = None
        if american is not None and line is not None:
            out.append({"sportsbook": book_key, "market": "TOTAL",
                        "selection": sel, "line": line, "american": american})
    return out


def discover(payload: dict[str, Any]) -> list[dict[str, Any]]:
    from fde_api.forward.odds import provider_team_to_code

    season = payload.get("season") or {}
    season_type = _SEASON_TYPE.get(season.get("type"), "REG")
    year = season.get("year") or datetime.now(UTC).year

    games: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c.get("team", {}) for c in comp.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue
        try:
            home = provider_team_to_code(teams["home"].get("displayName", ""))
            away = provider_team_to_code(teams["away"].get("displayName", ""))
        except KeyError:
            home = teams["home"].get("abbreviation", "")
            away = teams["away"].get("abbreviation", "")
        kickoff = datetime.fromisoformat(str(event.get("date")).replace("Z", "+00:00"))
        quotes: list[dict[str, Any]] = []
        for odds in comp.get("odds") or []:
            quotes.extend(_quotes_from_odds(odds, home=home, away=away))
        games.append({
            "provider_event_id": str(event.get("id")),
            "canonical_game_id": f"{year}_{season_type}_{kickoff:%m%d}_{away}_{home}",
            "name": event.get("name"),
            "season": year,
            "season_type": season_type,
            "home": home, "away": away,
            "kickoff_utc": kickoff,
            "venue": (comp.get("venue") or {}).get("fullName"),
            "neutral_site": bool(comp.get("neutralSite")),
            "status": (comp.get("status") or {}).get("type", {}).get("name"),
            "completed": bool((comp.get("status") or {}).get("type", {}).get("completed")),
            "scores": {
                c.get("homeAway"): (
                    int(c["score"]) if str(c.get("score", "")).strip().isdigit() else None
                )
                for c in comp.get("competitors", [])
            },
            "books": sorted({q["sportsbook"] for q in quotes}),
            "quotes": quotes,
        })
    return games


def persist(games: list[dict[str, Any]], *, data_mode_value: str) -> dict[str, int]:
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.db.forward_models import OddsQuote, ScheduleObservation
    from fde_api.forward.venues import resolve_venue

    counts = {"games_created": 0, "games_existing": 0, "quotes_written": 0,
              "quotes_duplicate": 0, "results_recorded": 0, "results_refused": 0}
    now = datetime.now(UTC)
    factory = sessionmaker(bind=get_engine(), future=True)

    with factory() as session:
        for g in games:
            venue = resolve_venue(stadium_id=None, stadium_name=g["venue"],
                                  neutral_site=g["neutral_site"])
            existing = session.scalars(
                select(ScheduleObservation).where(
                    ScheduleObservation.canonical_game_id == g["canonical_game_id"],
                    ScheduleObservation.data_mode == data_mode_value,
                ).limit(1)
            ).first()
            if existing is None:
                session.add(ScheduleObservation(
                    data_mode=data_mode_value,
                    canonical_game_id=g["canonical_game_id"],
                    provider=PROVIDER,
                    provider_game_id=g["provider_event_id"],
                    season=g["season"],
                    season_type=g["season_type"],
                    week=0,
                    home_team_id=g["home"],
                    away_team_id=g["away"],
                    kickoff_utc=g["kickoff_utc"],
                    venue_timezone=None,
                    # Attempt resolution rather than asserting None. It
                    # will often fail here - the governed table resolves
                    # international venues by name, and domestic ones
                    # arrive with an id from the schedule feed that this
                    # provider does not supply - and an unresolved venue is
                    # then correctly reported by `unmapped_venues`. What
                    # matters is that the gap is discovered rather than
                    # declared.
                    stadium_id=(venue.id if venue else None),
                    stadium_name=g["venue"],
                    neutral_site=g["neutral_site"],
                    international=False,
                    game_status=_GAME_STATUS.get(g["status"] or "", "UNRESOLVED"),
                    # Over the fields that define the fixture, so a later
                    # observation of the same game can be compared rather
                    # than blindly appended.
                    content_hash=hashlib.sha256(json.dumps({
                        "game": g["canonical_game_id"], "home": g["home"],
                        "away": g["away"], "kickoff": g["kickoff_utc"].isoformat(),
                        "venue": g["venue"], "neutral": g["neutral_site"],
                        "season_type": g["season_type"],
                    }, sort_keys=True).encode()).hexdigest(),
                    source_manifest_version=SOURCE_VERSION,
                    source_updated_at=now,
                    observed_at=now,
                ))
                counts["games_created"] += 1
            else:
                counts["games_existing"] += 1

            for q in g["quotes"]:
                # The capture instant is deliberately NOT in the hash. It
                # used to be, so two runs over an unchanged market produced
                # different hashes, the duplicate check never matched, and
                # every re-run appended a full set of rows - inventing price
                # movement that never happened and growing without bound.
                # Identity here is the price itself: book, market, side,
                # line, odds, for this game.
                raw = json.dumps({**q, "game": g["canonical_game_id"]}, sort_keys=True)
                raw_hash = hashlib.sha256(raw.encode()).hexdigest()
                if session.scalars(
                    select(OddsQuote).where(OddsQuote.raw_hash == raw_hash).limit(1)
                ).first():
                    counts["quotes_duplicate"] += 1
                    continue
                session.add(OddsQuote(
                    data_mode=data_mode_value,
                    canonical_game_id=g["canonical_game_id"],
                    provider=PROVIDER,
                    # LIVE: these are real prices from a real book, fetched
                    # now. Calling them FIXTURE would be a lie in the other
                    # direction.
                    provider_mode="LIVE",
                    provider_event_id=g["provider_event_id"],
                    sportsbook=q["sportsbook"],
                    market=q["market"],
                    selection=q["selection"],
                    line=q["line"],
                    american=q["american"],
                    decimal_odds=_american_to_decimal(q["american"]),
                    is_live=False,
                    provider_timestamp=None,
                    observed_at=now,
                    request_id=None,
                    raw_hash=raw_hash,
                ))
                counts["quotes_written"] += 1
        session.commit()

    # Results go through the governed path, not a direct write. It refuses
    # a score for a game it believes was never played and returns a typed
    # disposition rather than leaving the caller to infer what happened.
    # Discarding a final score that the provider had already handed us was
    # simply losing data the system has a place for.
    from fde_api.forward.modes import DataMode
    from fde_api.forward.results import (
        ResultIngestionError,
        ResultObservation,
        ingest_result,
    )

    with factory() as session:
        for g in games:
            if not g.get("completed"):
                continue
            home, away = g["scores"].get("home"), g["scores"].get("away")
            if home is None or away is None:
                continue
            try:
                _obs, disposition = ingest_result(
                    session,
                    ResultObservation(
                        canonical_game_id=g["canonical_game_id"],
                        home_score=home, away_score=away,
                        observed_at=now, provider=PROVIDER,
                    ),
                    data_mode=DataMode(data_mode_value),
                )
                counts["results_recorded"] += 1 if disposition in (
                    "recorded", "corrected") else 0
            except ResultIngestionError:
                # Refusal is a legitimate outcome, not a crash: the guard
                # exists so a score is never attached to a game the record
                # says was not played.
                counts["results_refused"] += 1
        session.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dates", default=None, help="YYYYMMDD")
    parser.add_argument("--data-mode", default="LIVE_RESEARCH",
                        choices=["DEMO", "LIVE_RESEARCH"])
    args = parser.parse_args()

    payload = fetch(args.dates)
    games = discover(payload)

    print(f"\n=== ESPN PUBLIC SCOREBOARD  ({len(games)} game(s)) ===")
    print("    real market data, provider=espn - NOT The Odds API, not bet365\n")
    for g in games:
        print(f"  {g['name']}")
        print(f"    {g['away']} @ {g['home']}   kickoff {g['kickoff_utc'].isoformat()}")
        print(f"    {g['season']} {g['season_type']}  venue {g['venue']}  "
              f"neutral={g['neutral_site']}  status={g['status']}")
        print(f"    id {g['canonical_game_id']}")
        print(f"    books: {g['books'] or 'none published'}")
        for q in g["quotes"]:
            line = "" if q["line"] is None else f" {q['line']:+g}"
            print(f"      {q['sportsbook']:12} {q['market']:9} {q['selection']:5}"
                  f"{line:>7}  {q['american']:+d}")
        if len(g["books"]) < 3:
            print(f"    NOTE: {len(g['books'])} book(s). Consensus needs 3+; "
                  "this game will report DATA_INCOMPLETE, correctly.")
        print()

    if args.dry_run:
        print("  dry run: nothing written\n")
        return 0

    counts = persist(games, data_mode_value=args.data_mode)
    print(f"  games created {counts['games_created']}  existing {counts['games_existing']}")
    print(f"  quotes written {counts['quotes_written']}  "
          f"duplicate {counts['quotes_duplicate']}")
    print(f"  results recorded {counts['results_recorded']}  "
          f"refused {counts['results_refused']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
