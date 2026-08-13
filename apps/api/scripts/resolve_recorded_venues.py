"""Fill in `stadium_id` on schedule observations whose venue now resolves.

WHY THIS IS NEEDED AT ALL

    A schedule observation's `content_hash` is taken over what the SOURCE
    said — game, teams, kickoff, venue NAME, neutral flag, season type. It
    does not include `stadium_id`, and it should not: the id is OUR
    resolution of the name, not something the provider stated. An
    observation records what a source said at an instant, and re-recording
    it because our mapping table improved would be inventing a provider
    observation that never happened.

    The consequence is that the resolved id is frozen at whatever the
    mapping knew when the row was first written. Fourteen ESPN games were
    recorded before `resolve_venue` could resolve a domestic name, so they
    carry NULL, and re-running the capture cannot fix them — the payload is
    unchanged, so the hash is unchanged, so no new observation is written.

WHAT THIS IS AND IS NOT

    A TRANSCRIPTION correction, exactly as `fix_espn_spread_convention.py`
    is. The provider said "Mercedes-Benz Stadium" and still does; only our
    transcription of that into a governed id was missing. Nothing about the
    observation changes.

    It is NOT a revision. A row that already carries an id is never
    touched, even when the name would now resolve to a different one —
    that would be changing an answer rather than supplying a missing one,
    and it is reported as a refusal for a person to look at.

Run:
  python scripts/resolve_recorded_venues.py --dry-run
  python scripts/resolve_recorded_venues.py
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def plan(session) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows to fill in, and reasons to refuse."""
    from sqlalchemy import select

    from fde_api.db.forward_models import ScheduleObservation
    from fde_api.forward.venues import resolve_by_name, resolve_venue

    rows = list(session.scalars(select(ScheduleObservation)))
    changes: list[dict[str, Any]] = []
    refusals: list[str] = []

    for r in rows:
        if r.stadium_id:
            # Already answered. Compare against what the NAME alone says:
            # `resolve_venue` honours the recorded id, which is the thing
            # being checked, so it would always agree with itself. Report a
            # disagreement and never act on it — overwriting an existing id
            # is a revision, not a transcription, and needs a person.
            by_name = resolve_by_name(r.stadium_name)
            if by_name is not None and by_name.id != r.stadium_id:
                refusals.append(
                    f"{r.canonical_game_id} obs {r.id}: recorded {r.stadium_id} "
                    f"but {r.stadium_name!r} resolves to {by_name.id}; "
                    "a revision, not a transcription"
                )
            continue
        venue = resolve_venue(
            stadium_id=None, stadium_name=r.stadium_name,
            neutral_site=bool(r.neutral_site),
        )
        if venue is None:
            refusals.append(
                f"{r.canonical_game_id} obs {r.id}: {r.stadium_name!r} still "
                "does not resolve; left unmapped and reported"
            )
            continue
        changes.append({
            "observation_id": r.id, "game": r.canonical_game_id,
            "name": r.stadium_name, "to": venue.id,
        })
    return changes, refusals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.db.forward_models import ScheduleObservation

    with sessionmaker(bind=get_engine(), future=True)() as session:
        changes, refusals = plan(session)

        print(f"\n=== VENUE TRANSCRIPTION ({len(changes)} row(s)) ===\n")
        for c in changes:
            print(f"  {c['game']:26} obs {c['observation_id']:>5}  "
                  f"{c['name']!r} -> {c['to']}")
        if refusals:
            print(f"\n  REFUSED ({len(refusals)}):")
            for r in refusals:
                print(f"    - {r}")

        if args.dry_run:
            print("\n  dry run: nothing written\n")
            return 0
        if not changes:
            print("\n  nothing to transcribe\n")
            return 0

        for c in changes:
            row = session.get(ScheduleObservation, c["observation_id"])
            if row is not None:
                row.stadium_id = c["to"]
        session.commit()
        print(f"\n  wrote {len(changes)} stadium id(s)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
