"""Final results as observations, not as a mutation of the schedule row.

A result is something a provider told us at a moment, which is exactly what
every other record in this system is. Storing it by overwriting the
schedule row would destroy the two facts that matter afterwards: WHEN the
result became knowable, and what was believed before it did.

So a result is appended as a new `ScheduleObservation` with status FINAL
that supersedes its predecessor. That gives point-in-time enforcement for
free — a prediction whose cutoff precedes the observation cannot see it,
because the observation did not exist at that cutoff.

Rules enforced here rather than left to callers:

  * A result observed before kickoff is refused. It is either a clock
    error or a fabrication, and either way it would let a prediction see an
    outcome that had not happened.
  * A postponed or cancelled game has no result. Recording one would state
    that a game which was never played had a score.
  * Ingestion is idempotent: the same score observed again produces no new
    row. A DIFFERENT score produces a correction - a new version - never an
    edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ScheduleObservation
from fde_api.forward.modes import DataMode
from fde_api.forward.schedule import current_schedule_state

FINAL = "FINAL"
NOT_PLAYED = ("CANCELLED", "POSTPONED")


class ResultIngestionError(ValueError):
    """A result that would be untrue or unusable if stored."""


@dataclass(frozen=True)
class ResultObservation:
    """One provider statement about how a game finished."""

    canonical_game_id: str
    home_score: int
    away_score: int
    observed_at: datetime
    provider: str = "fixture"

    @property
    def is_push_eligible(self) -> bool:
        """A tie is not a push, but it is the case that produces one on the
        spread and total, so it is worth naming rather than inferring."""
        return self.home_score == self.away_score


def _score_of(obs: ScheduleObservation) -> tuple[int | None, int | None]:
    """Scores are carried in the change summary of a FINAL observation.

    `schedule_observations` has no score columns - it describes when and
    where a game is played, not how it ended. Rather than widen it for one
    consumer, the FINAL observation records the score in its structured
    change summary, which is already the field that says what changed.
    """
    summary = obs.change_summary or ""
    marker = "final "
    if marker not in summary:
        return None, None
    try:
        tail = summary.split(marker, 1)[1].split()[0]
        home_s, away_s = tail.split("-")
        return int(home_s), int(away_s)
    except (ValueError, IndexError):
        return None, None


def final_observation(
    session: Session,
    *,
    canonical_game_id: str,
    data_mode: DataMode,
    as_of: datetime | None = None,
) -> ScheduleObservation | None:
    """The newest FINAL observation knowable at `as_of`.

    `as_of` filters on `observed_at`, which is the whole point: a result
    observed after a cutoff is not visible at that cutoff, so a prediction
    generated then cannot use it.
    """
    stmt = select(ScheduleObservation).where(
        ScheduleObservation.canonical_game_id == canonical_game_id,
        ScheduleObservation.data_mode == data_mode.value,
        ScheduleObservation.game_status == FINAL,
    )
    if as_of is not None:
        stmt = stmt.where(ScheduleObservation.observed_at <= as_of)
    rows = list(session.scalars(stmt))
    if not rows:
        return None
    # (observed_at, id) so two observations at the same instant still order
    # deterministically. Ordering by time alone made a simultaneous pair
    # resolve differently on different runs.
    rows.sort(key=lambda r: (r.observed_at, r.id))
    return rows[-1]


def ingest_result(
    session: Session,
    result: ResultObservation,
    *,
    data_mode: DataMode,
    correction_reason: str | None = None,
) -> tuple[ScheduleObservation | None, str]:
    """Append a FINAL observation. Returns (observation, disposition).

    Dispositions: `recorded`, `unchanged`, `corrected`, `refused`. The
    caller gets a typed answer rather than having to infer what happened
    from whether a row came back.
    """
    prev = current_schedule_state(session, result.canonical_game_id, data_mode)
    if prev is None:
        raise ResultIngestionError(
            f"no schedule observation for {result.canonical_game_id}; a result cannot "
            "precede knowing the game exists"
        )
    if prev.game_status in NOT_PLAYED:
        # Recording a score for a game that was never played would state
        # something false, and the ledger downstream would settle on it.
        return None, "refused"
    if prev.kickoff_utc is None:
        raise ResultIngestionError(
            f"{result.canonical_game_id} has no kickoff; a result cannot be placed in time"
        )
    if result.observed_at.tzinfo is None:
        raise ResultIngestionError("observed_at must be timezone-aware")
    if result.observed_at < prev.kickoff_utc:
        raise ResultIngestionError(
            f"result for {result.canonical_game_id} observed at "
            f"{result.observed_at.isoformat()}, before kickoff "
            f"{prev.kickoff_utc.isoformat()}; a game cannot be final before it starts"
        )

    existing = final_observation(
        session, canonical_game_id=result.canonical_game_id, data_mode=data_mode
    )
    if existing is not None:
        home, away = _score_of(existing)
        if (home, away) == (result.home_score, result.away_score):
            return existing, "unchanged"
        if not (correction_reason or "").strip():
            raise ResultIngestionError(
                f"{result.canonical_game_id} already has a final score of {home}-{away}; "
                f"recording {result.home_score}-{result.away_score} is a correction and "
                "requires a reason"
            )

    summary = (
        f"final {result.home_score}-{result.away_score} from {result.provider}"
        + (f" (correction: {correction_reason})" if existing is not None else "")
    )
    obs = ScheduleObservation(
        data_mode=data_mode.value,
        canonical_game_id=result.canonical_game_id,
        provider=result.provider,
        provider_game_id=prev.provider_game_id,
        season=prev.season,
        season_type=prev.season_type,
        week=prev.week,
        home_team_id=prev.home_team_id,
        away_team_id=prev.away_team_id,
        kickoff_utc=prev.kickoff_utc,
        venue_timezone=prev.venue_timezone,
        stadium_id=prev.stadium_id,
        stadium_name=prev.stadium_name,
        neutral_site=prev.neutral_site,
        international=prev.international,
        game_status=FINAL,
        content_hash=(
            f"final:{result.canonical_game_id}:{result.home_score}-{result.away_score}"
        ),
        source_manifest_version=None,
        observed_at=result.observed_at,
        supersedes_id=prev.id,
        change_summary=summary,
    )
    session.add(obs)
    session.flush()
    return obs, ("corrected" if existing is not None else "recorded")


def result_scores(
    session: Session,
    *,
    canonical_game_id: str,
    data_mode: DataMode,
    as_of: datetime | None = None,
) -> tuple[int, int] | None:
    """The final score knowable at `as_of`, or None."""
    obs = final_observation(
        session, canonical_game_id=canonical_game_id, data_mode=data_mode, as_of=as_of
    )
    if obs is None:
        return None
    home, away = _score_of(obs)
    if home is None or away is None:
        return None
    return home, away


def describe(obs: ScheduleObservation) -> dict[str, Any]:
    home, away = _score_of(obs)
    return {
        "id": obs.id,
        "canonical_game_id": obs.canonical_game_id,
        "provider": obs.provider,
        "home_score": home,
        "away_score": away,
        "observed_at_utc": obs.observed_at.isoformat(),
        "supersedes_id": obs.supersedes_id,
        "is_tie": home is not None and home == away,
    }
