"""Point-in-time guards — the release-blocking integrity layer.

A record participates in a feature snapshot only when

    observed_at <= as_of_at

These helpers are the single chokepoint the feature builder and replay
framework route through; adversarial tests in tests/test_leakage.py
verify that future results, odds, injuries, and roster changes are
rejected rather than silently used.
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
