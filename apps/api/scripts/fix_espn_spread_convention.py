"""Re-encode ESPN spread rows written away-relative.

SPREAD lines are stored HOME-RELATIVE everywhere else: `_write_quote`
negates the away point so both rows of a book carry the home team's
number. The first version of the ESPN capture stored the away row as the
provider gave it, away-relative, which put two opposite conventions in one
column. A consensus drawing on both sources would have averaged a line
against its own mirror.

This is a TRANSCRIPTION correction, not a revision of an observation. The
market said CAR -1.5 and still does; only the encoding was wrong. That is
the sole reason it is acceptable to touch a raw quote at all, and the
reason this is a script that says what it did rather than an UPDATE typed
into a shell.

It refuses unless the rows match the defective shape exactly: an away row
whose line is the negation of its own book's home row. A row already
home-relative is left alone, so running it twice changes nothing.

Run:
  python scripts/fix_espn_spread_convention.py --dry-run
  python scripts/fix_espn_spread_convention.py
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def plan(session) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows to re-encode, and reasons to refuse."""
    from sqlalchemy import select

    from fde_api.db.forward_models import OddsQuote

    rows = list(
        session.scalars(
            select(OddsQuote).where(
                OddsQuote.provider == "espn",
                OddsQuote.market == "SPREAD",
            )
        )
    )
    by_game_book: dict[tuple[str, str], dict[str, OddsQuote]] = {}
    for q in rows:
        by_game_book.setdefault((q.canonical_game_id, q.sportsbook), {})[q.selection] = q

    changes: list[dict[str, Any]] = []
    refusals: list[str] = []
    for (game, book), sides in sorted(by_game_book.items()):
        home, away = sides.get("HOME"), sides.get("AWAY")
        if home is None or away is None:
            refusals.append(f"{game}/{book}: only one side present; cannot verify")
            continue
        if home.line is None or away.line is None:
            refusals.append(f"{game}/{book}: a side has no line")
            continue
        if away.line == home.line:
            continue  # already home-relative
        if away.line != -home.line:
            # Not the defect this script understands. Two rows that are
            # neither equal nor exact mirrors mean something else happened,
            # and guessing would be the very thing that caused this.
            refusals.append(
                f"{game}/{book}: away {away.line:+g} is not the mirror of "
                f"home {home.line:+g}; not the known defect"
            )
            continue
        changes.append({
            "quote_id": away.id, "game": game, "book": book,
            "from": away.line, "to": home.line,
        })
    return changes, refusals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.db.forward_models import OddsQuote

    factory = sessionmaker(bind=get_engine(), future=True)
    with factory() as session:
        changes, refusals = plan(session)

        print(f"\n=== ESPN SPREAD RE-ENCODING ({len(changes)} row(s)) ===\n")
        for c in changes:
            print(f"  quote {c['quote_id']}  {c['game']}  {c['book']}  "
                  f"AWAY {c['from']:+g} -> {c['to']:+g}")
        if refusals:
            print("\n  REFUSED:")
            for r in refusals:
                print(f"    - {r}")
        if not changes:
            print("  nothing to re-encode; every away row is already home-relative")

        if args.dry_run:
            print("\n  dry run: nothing written\n")
            return 0

        for c in changes:
            row = session.get(OddsQuote, c["quote_id"])
            if row is not None:
                row.line = c["to"]
        session.commit()
        print(f"\n  re-encoded {len(changes)} row(s)\n")
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
