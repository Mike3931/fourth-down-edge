"""Point-in-time guards — the release-blocking integrity layer.

A record participates in a feature snapshot only when

    observed_at <= as_of_at

WHERE THAT IS ACTUALLY ENFORCED, because this module used to claim to be
"the single chokepoint" everything routes through, and it is not:

  * `LookaheadError` and `assert_no_lookahead` ARE called on the real
    paths — `features/builder.py`, `features/history.py`, `pit/clock.py`,
    `backtest/walkforward.py`, `canonical/load_games.py` and
    `forward/injuries.py` all raise through them.
  * The row-level FILTERING is inline, not here. `LeagueHistory.rows_for`
    and `all_rows_at` apply `observed_at <= as_of_at` directly, and the
    forward modules apply it in SQL.
  * The rule that a record with NO observation instant is excluded —
    unknown provenance treated as future, never as safely-past — is
    enforced at load time in `LeagueHistory.load`, which skips any
    `TeamGameStat` whose `observed_at` is NULL.

  * `filter_to_cutoff` below is a correct implementation of that rule
    that NO production path calls. It is exercised only by
    tests/test_leakage.py. Stated plainly because this codebase has
    already been bitten once by guards that were thoroughly tested and
    never wired in — a safety-shaped function nobody calls invites the
    assumption that safety rests on it, and here it does not.

Adversarial tests in tests/test_leakage.py verify that future results,
odds, injuries, and roster changes are rejected rather than silently
used.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TypeVar

T = TypeVar("T")


class LookaheadError(RuntimeError):
    """Raised when data from after the cutoff would enter a computation."""


def _require_aware(ts: datetime, what: str) -> datetime:
    if ts.tzinfo is None:
        raise LookaheadError(f"{what} is timezone-naive; point-in-time comparison would be ambiguous")
    return ts


def assert_no_lookahead(observed_at: datetime, as_of_at: datetime, what: str = "record") -> None:
    _require_aware(observed_at, f"observed_at of {what}")
    _require_aware(as_of_at, "as_of_at")
    if observed_at > as_of_at:
        raise LookaheadError(
            f"{what} observed at {observed_at.isoformat()} would leak into snapshot as of {as_of_at.isoformat()}"
        )


def filter_to_cutoff(
    records: Iterable[T],
    as_of_at: datetime,
    observed_at_of: Callable[[T], datetime | None],
) -> list[T]:
    """Keep only records usable at the cutoff. A record with NO observation
    instant is excluded — unknown provenance is treated as future, never as
    safely-past."""
    _require_aware(as_of_at, "as_of_at")
    usable: list[T] = []
    for r in records:
        ts = observed_at_of(r)
        if ts is None or ts.tzinfo is None:
            continue
        if ts <= as_of_at:
            usable.append(r)
    return usable
