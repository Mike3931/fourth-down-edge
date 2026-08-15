"""Move impossible-instant records out of the live-research cohort.

WHAT IS WRONG WITH THEM

    27 records carry an `observed_at` a month ahead of the clock that wrote
    them: 24 `odds_quotes` and 3 `consensus_snapshots`, all stamped
    2026-09-10 22:55 for `2026_01_SF_LA`, all written during a fixture run
    on 2026-08-01. An observation instant is this engine's own clock, so a
    future one cannot be a real observation.

    They are invisible today — every point-in-time read excludes anything
    after its cutoff — and they stop being invisible on 2026-09-10, when
    wall time passes them and they become the freshest prices available
    for a game kicking off 100 minutes later.

WHY MOVING THE COHORT IS THE FIX AND RE-STAMPING IS NOT

    Their `provider_timestamp` carries the same future instant, so the
    fixture itself described that moment. Nothing was mis-transcribed: the
    capture faithfully recorded what a fixture said. What is wrong is that
    fixture output is sitting in LIVE_RESEARCH, which the project's
    standing rule forbids outright — fixture output must never be stored as
    live-provider output. `provider_mode` already says UNKNOWN_LEGACY.

    So this is a COHORT correction. The rows are kept, in full, and moved
    to DEMO, where every live-research query already excludes them by the
    `data_mode` filter it applies anyway. Nothing is deleted.

WHY THE IDENTITY IS NO LONGER RECOMPUTED

    It used to be. `cohort` is part of the CONSENSUS logical identity, and
    while the identity was fed `data_mode.value`, moving a row from
    LIVE_RESEARCH to DEMO genuinely changed its identity — so the stored
    hash was recomputed here.

    Since `d8f41c6a3b92` the identity carries the row's real `cohort`,
    which is a separate column and is NOT what this script moves. A data
    mode is where a record is filed; a cohort is which experiment it
    belongs to. Moving the first no longer touches the second, so there is
    no identity to recompute and this script does not invent one.

    The collision check stays, and now means what it says: a slot is
    (game, market, cutoff, method) within the destination mode. A move
    that would put two records there is refused rather than merged.

Run:
  python scripts/quarantine_future_observations.py --dry-run
  python scripts/quarantine_future_observations.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from typing import Any

LIVE = "LIVE_RESEARCH"
QUARANTINE = "DEMO"


def plan(session, *, now: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    from sqlalchemy import select

    from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote

    changes: list[dict[str, Any]] = []
    refusals: list[str] = []

    for q in session.scalars(
        select(OddsQuote).where(
            OddsQuote.data_mode == LIVE, OddsQuote.observed_at > now
        ).order_by(OddsQuote.id)
    ):
        changes.append({
            "table": "odds_quotes", "id": q.id, "game": q.canonical_game_id,
            "market": q.market, "selection": q.selection,
            "observed_at": q.observed_at, "new_logical_hash": None,
        })

    existing = {
        (c.canonical_game_id, c.market, c.observed_at, c.method_version)
        for c in session.scalars(
            select(ConsensusSnapshot).where(ConsensusSnapshot.data_mode == QUARANTINE)
        )
    }
    for c in session.scalars(
        select(ConsensusSnapshot).where(
            ConsensusSnapshot.data_mode == LIVE, ConsensusSnapshot.observed_at > now
        ).order_by(ConsensusSnapshot.id)
    ):
        slot = (c.canonical_game_id, c.market, c.observed_at, c.method_version)
        if slot in existing:
            refusals.append(
                f"consensus {c.id} ({c.canonical_game_id}/{c.market}): a "
                f"{QUARANTINE} snapshot already occupies this slot; moving it "
                "would create two records at one identity"
            )
            continue
        changes.append({
            "table": "consensus_snapshots", "id": c.id,
            "game": c.canonical_game_id, "market": c.market,
            "selection": "-", "observed_at": c.observed_at,
            # Always None now — see the module docstring. Kept in the plan
            # so a reader comparing this to an older run can see that the
            # recompute was removed rather than silently skipped.
            "new_logical_hash": None,
        })
    return changes, refusals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote

    now = datetime.now(UTC)
    with sessionmaker(bind=get_engine(), future=True)() as session:
        changes, refusals = plan(session, now=now)

        print(f"\n=== QUARANTINE FUTURE OBSERVATIONS ({len(changes)} row(s)) ===")
        print(f"    {LIVE} -> {QUARANTINE}; nothing is deleted\n")
        for c in changes:
            print(f"  {c['table']:20} {c['id']:>5}  {c['game']:22} "
                  f"{c['market']:10} {c['selection']:6} observed {c['observed_at']}")
        if refusals:
            print(f"\n  REFUSED ({len(refusals)}):")
            for r in refusals:
                print(f"    - {r}")

        if args.dry_run:
            print("\n  dry run: nothing written\n")
            return 0
        if not changes:
            print("\n  nothing to quarantine\n")
            return 0

        for c in changes:
            model = OddsQuote if c["table"] == "odds_quotes" else ConsensusSnapshot
            row = session.get(model, c["id"])
            if row is None:
                continue
            # The data mode, and nothing else. The stored identity hash is
            # deliberately left alone: it is a function of the row's
            # `cohort`, which this script does not move. There used to be a
            # rewrite here, correct while the identity was fed `data_mode`.
            row.data_mode = QUARANTINE
        session.commit()
        print(f"\n  moved {len(changes)} row(s) to {QUARANTINE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
