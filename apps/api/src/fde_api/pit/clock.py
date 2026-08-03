"""Historical replay clock and prediction horizons.

Horizons are defined relative to kickoff. For historical replay the
canonical source records no intra-week observation timeline (nflverse
carries final results and closing lines only), so horizon cutoffs are
deterministic offsets from kickoff — documented, fixed, and identical
for every game. CLOSING_CAPTURE exists for evaluation only and is never
a prediction input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from fde_api.pit.guards import LookaheadError, _require_aware


class PredictionHorizon(StrEnum):
    OPENING = "OPENING"
    EARLY_WEEK = "EARLY_WEEK"
    PRACTICE_UPDATE = "PRACTICE_UPDATE"
    FINAL_INJURY_REPORT = "FINAL_INJURY_REPORT"
    PREGAME = "PREGAME"
    CLOSING_CAPTURE = "CLOSING_CAPTURE"  # evaluation benchmark only


# Offsets before kickoff. OPENING ≈ the Tuesday lookahead window (6 days),
# PREGAME is 90 minutes before kick, CLOSING_CAPTURE is kickoff itself.
_HORIZON_OFFSETS: dict[PredictionHorizon, timedelta] = {
    PredictionHorizon.OPENING: timedelta(days=6),
    PredictionHorizon.EARLY_WEEK: timedelta(days=4),
    PredictionHorizon.PRACTICE_UPDATE: timedelta(days=2),
    PredictionHorizon.FINAL_INJURY_REPORT: timedelta(days=1),
    PredictionHorizon.PREGAME: timedelta(minutes=90),
    PredictionHorizon.CLOSING_CAPTURE: timedelta(0),
}

PREDICTION_HORIZONS: tuple[PredictionHorizon, ...] = tuple(
    h for h in PredictionHorizon if h is not PredictionHorizon.CLOSING_CAPTURE
)


def horizon_as_of(kickoff_utc: datetime, horizon: PredictionHorizon) -> datetime:
    _require_aware(kickoff_utc, "kickoff_utc")
    return kickoff_utc - _HORIZON_OFFSETS[horizon]


@dataclass
class ReplayClock:
    """Simulated 'now'. The replay loop owns one clock and only ever moves
    it forward; any attempt to rewind is a hard error because it would let
    later state leak into re-computed earlier snapshots."""

    now: datetime

    def __post_init__(self) -> None:
        _require_aware(self.now, "replay clock")

    def advance_to(self, instant: datetime) -> None:
        _require_aware(instant, "replay clock target")
        if instant < self.now:
            raise LookaheadError(
                f"Replay clock may not rewind ({self.now.isoformat()} -> {instant.isoformat()})"
            )
        self.now = instant

    def can_see(self, observed_at: datetime | None) -> bool:
        """Inclusive visibility: an observation made AT this instant counts.

        Correct for observation vintages — an odds snapshot, injury report,
        or weather forecast stamped exactly at the cutoff was genuinely
        available at the cutoff. Not correct for a completed-game RESULT;
        use `can_see_result` for that.
        """
        return observed_at is not None and observed_at.tzinfo is not None and observed_at <= self.now

    def can_see_result(self, observed_at: datetime | None) -> bool:
        """Strict visibility for completed-game results: `observed_at < now`.

        A result is only usable once it is strictly in the past. At exactly
        `now` it is not, and the distinction is not academic: when `now` is
        a game's own kickoff, an inclusive test would admit that game's own
        outcome into the state used to predict it.

        Making this strict means replay correctness no longer depends on
        `RESULT_AVAILABILITY_OFFSET` being positive. Even a zero offset
        cannot leak a game into its own prediction through this predicate.
        """
        return observed_at is not None and observed_at.tzinfo is not None and observed_at < self.now
