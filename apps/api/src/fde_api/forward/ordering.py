"""One tie-break rule for "the newest record", in one place.

Three services needed the same thing: given rows sharing a timestamp, pick
one deterministically. All three wrote `rows.sort(key=lambda r: (r.observed_at,
r.id))` inline, which is correct and also invisible - a mutation sweep found
that removing the `.id` half changed nothing that any test could see.

It changed nothing because SQLite happens to return rows in insertion order,
which coincides with id order, and Python's sort is stable. So the guard was
real, load-bearing on PostgreSQL, and completely unprotected. Extracting it
makes it something a test can hand a deliberately shuffled list to.

The rule: order by (timestamp, id) and take the last. Ties resolve to the
highest id, always, on every backend.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

T = TypeVar("T")


def newest(
    rows: Sequence[T],
    *,
    when: Callable[[T], datetime],
    ident: Callable[[T], Any],
) -> T | None:
    """The newest row, with ties broken by identity.

    Returns None for an empty sequence rather than raising: "there is no
    newest row" is an ordinary answer at every call site here, and forcing
    each one to guard against an exception would add noise without adding
    safety.
    """
    if not rows:
        return None
    return max(rows, key=lambda r: (when(r), ident(r)))


def order_by_time_then_id(
    rows: Sequence[T],
    *,
    when: Callable[[T], datetime],
    ident: Callable[[T], Any],
) -> list[T]:
    """Every row, oldest first, ties resolved by identity."""
    return sorted(rows, key=lambda r: (when(r), ident(r)))
