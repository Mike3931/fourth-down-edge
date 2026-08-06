"""Create slate entries for provider events the schedule source does not carry.

The schedule source (nflverse `games.csv`) has never carried preseason
games — every season from 1999 to 2026 contains only REG/WC/DIV/CON/SB and
zero July or August fixtures. So a preseason game cannot be ingested from
it at all, and `capture_odds` drops such events into `unmapped_events`
without writing a single quote, because it matches provider events against
the observed schedule and finds nothing.

This bridges that gap for games the schedule source omits, and does it
without pretending the two sources are the same thing:

  * The observation is stamped `provider="the-odds-api"`, not `"nflverse"`.
    A game whose existence is only attested by an odds provider is a
    weaker record than one from the schedule feed, and the row says so.

  * `season_type` comes from the provider's own commence time relative to
    the known regular season, and is `PRE` when it falls before it. It is
    never guessed as REG, because REG is what every downstream default
    assumes and a wrong guess there is invisible.

  * It refuses to touch a game the schedule source already covers.
    Duplicating a fixture across two provenances is how a slate ends up
    with two versions of one game and no way to say which is right.

WHAT THIS DOES NOT DO
    It does not make preseason analysable. The models are regular-season
    models; features come from prior regular-season games and calibration
    was fitted on regular-season outcomes. Creating the slate entry lets
    real market data be captured and displayed. It does not make a model
    edge for such a game meaningful, and the decision layer should keep
    reporting DATA_INCOMPLETE.

The provider key is read from FDE_ODDS_API_KEY by the adapter itself and
is never read, printed, or accepted as an argument here.

Run:
  python scripts/ingest_provider_events.py --dry-run
  python scripts/ingest_provider_events.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

# Kickoffs before this are not regular-season 2026 fixtures.
REGULAR_SEASON_2026_OPENS = datetime(2026, 9, 8, tzinfo=UTC)


def _canonical_id(*, season: int, away: str, home: str, kickoff: datetime) -> str:
    """A stable id for a game the schedule source never gave one.

    Prefixed so it can never be confused with a schedule-derived id, and
    dated so two preseason meetings of the same teams stay distinct.
    """
    return f"{season}_PRE_{kickoff:%m%d}_{away}_{home}"


def discover(*, data_mode_value: str) -> dict[str, Any]:
    """Fetch current provider events and classify each against the slate."""
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.forward.modes import DataMode
    from fde_api.forward.odds import (
        TheOddsApiProvider,
        _match_canonical_game,
        provider_team_to_code,
    )

    provider = TheOddsApiProvider()
    if not provider.configured:
        raise SystemExit(
            "FDE_ODDS_API_KEY is not set. This script cannot discover which "
            "games exist without it, and will not guess."
        )

    payload, headers = provider.fetch_odds()
    data_mode = DataMode(data_mode_value)

    known: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []
    unmappable: list[str] = []

    factory = sessionmaker(bind=get_engine(), future=True)
    with factory() as session:
        for event in payload:
            try:
                home = provider_team_to_code(event.get("home_team", ""))
                away = provider_team_to_code(event.get("away_team", ""))
            except KeyError as exc:
                unmappable.append(f"{event.get('id')}: unknown team {exc}")
                continue
            commence = event.get("commence_time")
            if not commence:
                unmappable.append(f"{event.get('id')}: no commence_time")
                continue
            kickoff = datetime.fromisoformat(commence.replace("Z", "+00:00"))

            matched = _match_canonical_game(
                session, home_team=home, away_team=away,
                commence_time=kickoff, data_mode=data_mode,
            )
            record = {
                "provider_event_id": event.get("id"),
                "away": away, "home": home,
                "kickoff_utc": kickoff.isoformat(),
                "bookmakers": len(event.get("bookmakers", [])),
            }
            if matched:
                known.append({**record, "canonical_game_id": matched})
            else:
                season = kickoff.year if kickoff.month >= 3 else kickoff.year - 1
                season_type = "PRE" if kickoff < REGULAR_SEASON_2026_OPENS else "REG"
                new.append({
                    **record,
                    "season": season,
                    "season_type": season_type,
                    "canonical_game_id": _canonical_id(
                        season=season, away=away, home=home, kickoff=kickoff),
                })

    return {
        "fetched_at_utc": datetime.now(UTC).isoformat(),
        "events_seen": len(payload),
        "already_in_slate": known,
        "not_in_slate": new,
        "unmappable": unmappable,
        "quota_remaining": headers.get("x-requests-remaining"),
        "quota_used": headers.get("x-requests-used"),
    }


def ingest(discovered: dict[str, Any], *, data_mode_value: str) -> list[str]:
    """Write slate entries for the events the schedule source omitted."""
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.db.forward_models import ScheduleObservation

    created: list[str] = []
    factory = sessionmaker(bind=get_engine(), future=True)
    now = datetime.now(UTC)
    with factory() as session:
        for e in discovered["not_in_slate"]:
            session.add(ScheduleObservation(
                data_mode=data_mode_value,
                canonical_game_id=e["canonical_game_id"],
                # NOT "nflverse". A fixture attested only by an odds
                # provider is a weaker record and the row must say so.
                provider="the-odds-api",
                provider_game_id=e["provider_event_id"],
                season=e["season"],
                season_type=e["season_type"],
                week=0,
                home_team_id=e["home"],
                away_team_id=e["away"],
                kickoff_utc=datetime.fromisoformat(e["kickoff_utc"]),
                venue_timezone=None,
                stadium_id=None,
                stadium_name=None,
                neutral_site=False,
                international=False,
                game_status="SCHEDULED",
                content_hash=None,
                source_manifest_version="provider-event-bridge-v1",
                source_updated_at=now,
                observed_at=now,
            ))
            created.append(e["canonical_game_id"])
        session.commit()
    return created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be created; write nothing")
    parser.add_argument("--data-mode", default="LIVE_RESEARCH",
                        choices=["DEMO", "LIVE_RESEARCH"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    found = discover(data_mode_value=args.data_mode)

    if args.json:
        print(json.dumps(found, indent=2, sort_keys=True, default=str))

    print(f"\n=== PROVIDER EVENTS ({found['events_seen']} seen) ===")
    print(f"  credits remaining {found['quota_remaining']}  used {found['quota_used']}\n")
    if found["already_in_slate"]:
        print("  already in the slate:")
        for e in found["already_in_slate"]:
            print(f"    {e['kickoff_utc']}  {e['away']} @ {e['home']}  "
                  f"{e['canonical_game_id']}  ({e['bookmakers']} books)")
    if found["not_in_slate"]:
        print("\n  NOT in the slate (the schedule source does not carry these):")
        for e in found["not_in_slate"]:
            print(f"    {e['kickoff_utc']}  {e['away']} @ {e['home']}  "
                  f"{e['season_type']}  -> {e['canonical_game_id']}  "
                  f"({e['bookmakers']} books)")
    if found["unmappable"]:
        print("\n  unmappable:")
        for u in found["unmappable"]:
            print(f"    {u}")

    if args.dry_run:
        print("\n  dry run: nothing was written\n")
        return 0
    if not found["not_in_slate"]:
        print("\n  nothing to create; every event is already in the slate\n")
        return 0

    created = ingest(found, data_mode_value=args.data_mode)
    print(f"\n  created {len(created)} slate entr(ies), provider=the-odds-api:")
    for c in created:
        print(f"    {c}")
    print("\n  These are attested by an ODDS provider, not the schedule feed.")
    print("  Market data can now be captured for them. The models remain")
    print("  regular-season models; a preseason edge number is not meaningful.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
