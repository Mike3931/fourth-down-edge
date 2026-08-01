"""Operating modes — the structural separation between demonstration and
live research data.

This is a safety boundary, not a display preference. Every forward record
carries its mode, queries filter by it, and mixing modes in one screen,
report, metric, or API response is a defect. There is deliberately no
"fall back to demo when live is missing" path: absent live data yields
DATA INCOMPLETE.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class DataMode(StrEnum):
    DEMO = "DEMO"
    LIVE_RESEARCH = "LIVE_RESEARCH"


DEMO_BANNER = "DEMONSTRATION DATA — NOT FOR REAL-WORLD DECISIONS"
LIVE_RESEARCH_BANNER = "LIVE RESEARCH MODE — FORWARD MODEL NOT APPROVED FOR REAL-MONEY DECISIONS"

# Not a mode: a per-record/per-screen state meaning "required real inputs are
# missing, stale, conflicting, or unsupported". Kept here so the vocabulary
# lives in one place.
DATA_INCOMPLETE = "DATA_INCOMPLETE"


def mode_banner(mode: DataMode) -> str:
    return DEMO_BANNER if mode is DataMode.DEMO else LIVE_RESEARCH_BANNER


class ModeMixingError(RuntimeError):
    """Raised when records of different modes would be combined."""


def assert_single_mode(modes: Iterable[str | DataMode], context: str = "result set") -> DataMode:
    """Verify an iterable of modes contains exactly one distinct value.

    Used at every aggregation boundary (reports, metrics, API payloads) so
    that mixing is caught structurally rather than reviewed for.
    """
    distinct = {DataMode(m) for m in modes}
    if not distinct:
        raise ModeMixingError(f"{context} has no records; mode is undetermined")
    if len(distinct) > 1:
        raise ModeMixingError(
            f"{context} mixes data modes {sorted(m.value for m in distinct)}; "
            "demo and live research records must never be combined"
        )
    return distinct.pop()
