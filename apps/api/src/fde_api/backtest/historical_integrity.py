"""Data-integrity scan over every historical game a backtest can consume.

The sequential replay was hardened to refuse a game whose result is
stamped at or before its own kickoff (see
`walkforward.sequential_ratings_moments`). That guard is forward-looking:
it says what will be refused from now on. It does not say whether any such
row was ever present in the data the published evaluation actually ran on.

This answers that. The condition required to preserve historical claims is

    result_observed_at > kickoff_utc

for every completed game. Anything else - equal, earlier, missing where a
result exists, timezone-naive, duplicated, or self-contradictory - is
reported and counted rather than skipped.

Three layers are inspected deliberately, because they disagree and only
one of them is what the backtest sees:

  * the RAW layer - the literal text SQLite holds. SQLite has no timezone
    type, so every timestamp is stored without a designator. This is
    normal, not a defect, and counting it as "naive" would be alarming
    and wrong.
  * the ORM layer. `Base.type_annotation_map` maps `datetime` to
    `UtcDateTime()`, so a plain `mapped_column()` on a `Mapped[datetime]`
    annotation still goes through that decorator and comes back UTC-aware.
    This is where the awareness requirement is actually met.
  * the LOADED layer, read through `LeagueHistory`, which is what the
    backtest consumes after `_aware_opt`.

The raw count is reported so nobody reads "0 naive" as a claim that
SQLite stores offsets. It does not; the decoder supplies them.

Output is machine-readable and content-hashed so a certification can cite
it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.models import Game


@dataclass
class ScanResult:
    games_scanned: int = 0
    completed_games: int = 0
    min_offset_seconds: float | None = None
    max_offset_seconds: float | None = None
    min_offset_game: str | None = None
    max_offset_game: str | None = None
    equal_to_kickoff: int = 0
    before_kickoff: int = 0
    missing_result_time: int = 0
    missing_kickoff: int = 0
    raw_without_tz_designator: int = 0  # expected to equal 2x completed on SQLite
    naive_after_orm_decode: int = 0
    naive_after_load: int = 0
    duplicate_game_ids: int = 0
    conflicting_results: int = 0
    rejected: int = 0
    rejected_ids: list[str] = field(default_factory=list)
    seasons: dict[str, int] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        """The condition required to preserve historical claims."""
        return (
            self.rejected == 0
            and self.equal_to_kickoff == 0
            and self.before_kickoff == 0
            and self.missing_result_time == 0
            and self.duplicate_game_ids == 0
            and self.conflicting_results == 0
            and self.naive_after_load == 0
            and self.naive_after_orm_decode == 0
        )


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def scan(session: Session) -> ScanResult:
    r = ScanResult()
    rows = list(session.scalars(select(Game)))
    r.games_scanned = len(rows)

    ids = Counter(g.id for g in rows)
    r.duplicate_game_ids = sum(c - 1 for c in ids.values() if c > 1)

    for g in rows:
        r.seasons[str(g.season)] = r.seasons.get(str(g.season), 0) + 1

        completed = g.home_score is not None and g.away_score is not None
        if completed:
            r.completed_games += 1

        # What the ORM hands back, after UtcDateTime decoding.
        for ts in (g.kickoff_utc, g.result_observed_at):
            if ts is not None and ts.tzinfo is None:
                r.naive_after_orm_decode += 1

        kickoff = _aware(g.kickoff_utc)
        observed = _aware(g.result_observed_at)

        if kickoff is None:
            r.missing_kickoff += 1
            r.rejected += 1
            r.rejected_ids.append(g.id)
            continue

        if completed and observed is None:
            r.missing_result_time += 1
            r.rejected += 1
            r.rejected_ids.append(g.id)
            continue

        # A result without scores, or scores that contradict themselves.
        if observed is not None and not completed:
            r.conflicting_results += 1
            r.rejected += 1
            r.rejected_ids.append(g.id)
            continue

        if observed is None:
            continue  # unplayed game: nothing to leak, nothing to measure

        delta = (observed - kickoff).total_seconds()
        if delta == 0:
            r.equal_to_kickoff += 1
            r.rejected += 1
            r.rejected_ids.append(g.id)
        elif delta < 0:
            r.before_kickoff += 1
            r.rejected += 1
            r.rejected_ids.append(g.id)

        if r.min_offset_seconds is None or delta < r.min_offset_seconds:
            r.min_offset_seconds, r.min_offset_game = delta, g.id
        if r.max_offset_seconds is None or delta > r.max_offset_seconds:
            r.max_offset_seconds, r.max_offset_game = delta, g.id

    return r


def scan_raw_layer(session: Session, r: ScanResult) -> ScanResult:
    """Count stored timestamps carrying no timezone designator.

    On SQLite this is expected to be every one of them - the engine has no
    timezone type. Reported rather than hidden so "0 naive" elsewhere is
    not mistaken for a claim about storage.
    """
    from sqlalchemy import text

    if session.bind is None or session.bind.dialect.name != "sqlite":
        r.raw_without_tz_designator = -1  # not meaningful on this dialect
        return r
    q = text(
        "SELECT COUNT(*) FROM games WHERE "
        "(kickoff_utc IS NOT NULL AND kickoff_utc NOT LIKE '%+%' AND kickoff_utc NOT LIKE '%Z') "
        "OR (result_observed_at IS NOT NULL AND result_observed_at NOT LIKE '%+%' "
        "AND result_observed_at NOT LIKE '%Z')"
    )
    r.raw_without_tz_designator = int(session.execute(q).scalar() or 0)
    return r


def scan_loaded_layer(r: ScanResult) -> ScanResult:
    """Count timestamps still naive after LeagueHistory coercion."""
    from fde_api.db.engine import get_session
    from fde_api.features.history import LeagueHistory

    history = LeagueHistory.load(get_session())
    naive = 0
    for g in history.games:
        if g.kickoff is not None and g.kickoff.tzinfo is None:
            naive += 1
        if g.result_observed_at is not None and g.result_observed_at.tzinfo is None:
            naive += 1
    r.naive_after_load = naive
    return r


def report_payload(r: ScanResult) -> dict[str, Any]:
    payload = asdict(r)
    payload["rejected_ids"] = sorted(payload["rejected_ids"])[:200]
    payload["required_condition"] = "result_observed_at > kickoff_utc for every completed game"
    payload["passes"] = r.passes
    return payload


def report_json(r: ScanResult) -> str:
    # sort_keys so the hash is stable across runs and platforms.
    return json.dumps(report_payload(r), indent=2, sort_keys=True, default=str)


def report_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="write the JSON report here")
    args = parser.parse_args(argv)

    from fde_api.db.engine import get_session

    session = get_session()
    r = scan(session)
    scan_raw_layer(session, r)
    scan_loaded_layer(r)
    text = report_json(r)
    digest = report_hash(text)

    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text, encoding="utf-8")

    print(text)
    print(f"\nsha256: {digest}")
    print(f"required condition holds: {r.passes}")
    return 0 if r.passes else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
