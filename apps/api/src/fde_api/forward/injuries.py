"""Prospective injury capture and availability model v0.

No restricted site is scraped. Observations arrive through a structured
manual-entry workflow or a licensed provider, each carrying its source
category, the instant it was observed, and its verification status.

Availability v0 is deliberately a *rules* layer producing RANGES. We do
not have the data to justify point estimates of player value, so the
model says "expected active, 0.90-0.99, HIGH confidence" rather than
inventing 0.943. Quarterbacks are handled separately because starter
uncertainty changes the game distribution far more than any other
position — and when the starter cannot be resolved, the policy turns
that into DATA INCOMPLETE rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import AvailabilityAssessment, InjuryObservation
from fde_api.forward.modes import DataMode
from fde_api.pit.guards import LookaheadError
from fde_api.util import utc_now


class SourceCategory(StrEnum):
    OFFICIAL_VERIFIED = "OFFICIAL_VERIFIED"
    LICENSED_PROVIDER = "LICENSED_PROVIDER"
    USER_VERIFIED = "USER_VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class AvailabilityState(StrEnum):
    EXPECTED_ACTIVE = "EXPECTED_ACTIVE"
    ACTIVE_WITH_RESTRICTION_RISK = "ACTIVE_WITH_RESTRICTION_RISK"
    GAME_TIME_DECISION = "GAME_TIME_DECISION"
    DOUBTFUL = "DOUBTFUL"
    EXPECTED_INACTIVE = "EXPECTED_INACTIVE"
    CONFIRMED_INACTIVE = "CONFIRMED_INACTIVE"
    UNKNOWN = "UNKNOWN"


# Source categories admissible as PRODUCTION features. Unverified reports
# may be displayed in a research context but never silently feed a model.
PRODUCTION_SOURCE_CATEGORIES = frozenset(
    {SourceCategory.OFFICIAL_VERIFIED, SourceCategory.LICENSED_PROVIDER, SourceCategory.USER_VERIFIED}
)

VALID_PRACTICE = {"DNP", "LIMITED", "FULL", None}
VALID_DESIGNATION = {"OUT", "DOUBTFUL", "QUESTIONABLE", "NONE", None}


@dataclass(frozen=True)
class StateProfile:
    state: AvailabilityState
    active_low: float
    active_high: float
    snap_low: float | None
    snap_high: float | None
    confidence: str


# Ranges are intentionally wide. They encode the designation's meaning,
# not a fitted estimate — no forward sample exists yet to fit one.
_PROFILES: dict[AvailabilityState, StateProfile] = {
    AvailabilityState.EXPECTED_ACTIVE: StateProfile(
        AvailabilityState.EXPECTED_ACTIVE, 0.90, 0.99, 0.85, 1.00, "HIGH"),
    AvailabilityState.ACTIVE_WITH_RESTRICTION_RISK: StateProfile(
        AvailabilityState.ACTIVE_WITH_RESTRICTION_RISK, 0.75, 0.95, 0.50, 0.90, "MEDIUM"),
    AvailabilityState.GAME_TIME_DECISION: StateProfile(
        AvailabilityState.GAME_TIME_DECISION, 0.40, 0.70, 0.30, 0.80, "LOW"),
    AvailabilityState.DOUBTFUL: StateProfile(
        AvailabilityState.DOUBTFUL, 0.05, 0.30, 0.00, 0.40, "MEDIUM"),
    AvailabilityState.EXPECTED_INACTIVE: StateProfile(
        AvailabilityState.EXPECTED_INACTIVE, 0.00, 0.10, 0.00, 0.10, "MEDIUM"),
    AvailabilityState.CONFIRMED_INACTIVE: StateProfile(
        AvailabilityState.CONFIRMED_INACTIVE, 0.00, 0.00, 0.00, 0.00, "HIGH"),
    AvailabilityState.UNKNOWN: StateProfile(
        AvailabilityState.UNKNOWN, 0.00, 1.00, None, None, "NONE"),
}


class InjuryValidationError(ValueError):
    """Rejected observation — malformed, or dated in the future."""


def record_injury_observation(
    session: Session,
    *,
    canonical_game_id: str,
    team_id: str,
    player_id: str,
    report_date: str,
    observed_at: datetime,
    source_category: SourceCategory | str,
    practice_status: str | None = None,
    game_designation: str | None = None,
    body_part: str | None = None,
    source_reference: str | None = None,
    source_updated_at: datetime | None = None,
    confidence: str = "MEDIUM",
    user_id: str | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    now: datetime | None = None,
) -> InjuryObservation:
    """Append an injury observation, superseding any prior one for the same
    (game, player). Never revises history in place."""
    now = now or utc_now()
    cat = SourceCategory(source_category)

    if observed_at.tzinfo is None:
        raise InjuryValidationError("observed_at must be timezone-aware")
    if observed_at > now + timedelta(minutes=1):
        # An observation cannot be from the future; this is the guard that
        # stops "we knew on Tuesday" claims made on Sunday.
        raise LookaheadError(
            f"injury observation claims observed_at={observed_at.isoformat()} which is after now={now.isoformat()}"
        )
    if practice_status not in VALID_PRACTICE:
        raise InjuryValidationError(f"invalid practice_status {practice_status!r}")
    if game_designation not in VALID_DESIGNATION:
        raise InjuryValidationError(f"invalid game_designation {game_designation!r}")
    if not player_id or not player_id.strip():
        raise InjuryValidationError("player_id is required; players are never matched by name")

    prev = _latest_observation(session, canonical_game_id, player_id, data_mode)
    obs = InjuryObservation(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        team_id=team_id,
        player_id=player_id,
        report_date=report_date,
        body_part=body_part,
        practice_status=practice_status,
        game_designation=game_designation,
        source_category=cat.value,
        source_reference=source_reference,
        confidence=confidence,
        verification_status="VERIFIED" if cat in PRODUCTION_SOURCE_CATEGORIES else "UNVERIFIED",
        source_updated_at=source_updated_at,
        observed_at=observed_at,
        entered_at=now,
        user_id=user_id,
    )
    session.add(obs)
    session.flush()
    if prev is not None:
        prev.superseded_by_id = obs.id
    return obs


def _latest_observation(
    session: Session, game_id: str, player_id: str, data_mode: DataMode
) -> InjuryObservation | None:
    return session.scalars(
        select(InjuryObservation)
        .where(
            InjuryObservation.canonical_game_id == game_id,
            InjuryObservation.player_id == player_id,
            InjuryObservation.data_mode == data_mode.value,
        )
        .order_by(InjuryObservation.observed_at.desc(), InjuryObservation.id.desc())
        .limit(1)
    ).first()


def observations_as_of(
    session: Session,
    *,
    canonical_game_id: str,
    as_of_at: datetime,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    production_only: bool = True,
) -> list[InjuryObservation]:
    """Latest observation per player that was known at the cutoff.

    `production_only` drops UNVERIFIED reports: they may be shown in a
    research context but must never silently become model features.
    """
    stmt = select(InjuryObservation).where(
        InjuryObservation.canonical_game_id == canonical_game_id,
        InjuryObservation.data_mode == data_mode.value,
        InjuryObservation.observed_at <= as_of_at,
    )
    if production_only:
        stmt = stmt.where(InjuryObservation.source_category.in_([c.value for c in PRODUCTION_SOURCE_CATEGORIES]))
    latest: dict[str, InjuryObservation] = {}
    for o in session.scalars(stmt.order_by(InjuryObservation.observed_at, InjuryObservation.id)):
        latest[o.player_id] = o
    return list(latest.values())


def classify(obs: InjuryObservation | None) -> AvailabilityState:
    """Map a designation/practice pair to a conservative state."""
    if obs is None:
        return AvailabilityState.UNKNOWN
    d = (obs.game_designation or "").upper()
    p = (obs.practice_status or "").upper()
    if d == "OUT":
        return AvailabilityState.CONFIRMED_INACTIVE
    if d == "DOUBTFUL":
        return AvailabilityState.DOUBTFUL
    if d == "QUESTIONABLE":
        # Questionable + DNP is materially worse than questionable + full.
        if p == "DNP":
            return AvailabilityState.GAME_TIME_DECISION
        if p == "LIMITED":
            return AvailabilityState.ACTIVE_WITH_RESTRICTION_RISK
        return AvailabilityState.ACTIVE_WITH_RESTRICTION_RISK
    if d in ("NONE", ""):
        if p == "DNP":
            return AvailabilityState.GAME_TIME_DECISION
        if p == "LIMITED":
            return AvailabilityState.ACTIVE_WITH_RESTRICTION_RISK
        if p == "FULL":
            return AvailabilityState.EXPECTED_ACTIVE
    return AvailabilityState.UNKNOWN


def assess_player(
    session: Session,
    *,
    canonical_game_id: str,
    team_id: str,
    player_id: str,
    as_of_at: datetime,
    position: str | None = None,
    is_starting_qb: bool = False,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> AvailabilityAssessment:
    """Build one availability assessment at a cutoff."""
    obs = next(
        (
            o
            for o in observations_as_of(
                session, canonical_game_id=canonical_game_id, as_of_at=as_of_at, data_mode=data_mode
            )
            if o.player_id == player_id
        ),
        None,
    )
    state = classify(obs)
    profile = _PROFILES[state]
    freshness = (as_of_at - obs.observed_at).total_seconds() / 3600.0 if obs else None

    reason_parts = []
    if obs is None:
        reason_parts.append("no verified injury observation at cutoff")
    else:
        reason_parts.append(f"designation={obs.game_designation or 'NONE'}")
        reason_parts.append(f"practice={obs.practice_status or 'n/a'}")
        reason_parts.append(f"source={obs.source_category}")
        if freshness is not None:
            reason_parts.append(f"age={freshness:.1f}h")
    if is_starting_qb:
        reason_parts.append("starting QB: uncertainty propagates to the game distribution")

    assessment = AvailabilityAssessment(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        team_id=team_id,
        player_id=player_id,
        position=position,
        state=state.value,
        active_prob_low=profile.active_low,
        active_prob_high=profile.active_high,
        snap_share_low=profile.snap_low,
        snap_share_high=profile.snap_high,
        confidence_tier=profile.confidence,
        source_freshness_hours=freshness,
        reason="; ".join(reason_parts),
        missing_data=obs is None,
        is_starting_qb=is_starting_qb,
        as_of_at=as_of_at,
        created_at=utc_now(),
    )
    session.add(assessment)
    session.flush()
    return assessment


@dataclass
class QbResolution:
    """Whether the starting quarterback is known well enough to predict."""

    resolved: bool
    player_id: str | None
    state: AvailabilityState
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "player_id": self.player_id,
            "state": self.state.value,
            "reason": self.reason,
        }


def resolve_starting_qb(
    session: Session,
    *,
    canonical_game_id: str,
    team_id: str,
    expected_qb_id: str | None,
    as_of_at: datetime,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> QbResolution:
    """Decide whether the starter is resolved at a cutoff.

    A QB who is a game-time decision, doubtful, or inactive leaves the
    starter unresolved. Under the default policy that yields DATA
    INCOMPLETE — the alternative (explicit weighted starter scenarios) is
    a documented policy option, not a silent default.
    """
    if not expected_qb_id:
        return QbResolution(False, None, AvailabilityState.UNKNOWN,
                            "no expected starter could be derived from prior games")
    obs = next(
        (
            o
            for o in observations_as_of(
                session, canonical_game_id=canonical_game_id, as_of_at=as_of_at, data_mode=data_mode
            )
            if o.player_id == expected_qb_id
        ),
        None,
    )
    state = classify(obs)
    if state in (
        AvailabilityState.CONFIRMED_INACTIVE,
        AvailabilityState.EXPECTED_INACTIVE,
        AvailabilityState.DOUBTFUL,
        AvailabilityState.GAME_TIME_DECISION,
    ):
        return QbResolution(False, expected_qb_id, state,
                            f"expected starter is {state.value}; backup quality is not modeled in v0")
    if state is AvailabilityState.UNKNOWN and obs is not None:
        return QbResolution(False, expected_qb_id, state, "designation could not be interpreted")
    # No adverse observation: treat the prior-game starter as the starter.
    return QbResolution(True, expected_qb_id, state or AvailabilityState.EXPECTED_ACTIVE,
                        "no adverse verified observation at cutoff")


def availability_snapshot(
    session: Session,
    *,
    canonical_game_id: str,
    as_of_at: datetime,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> dict[str, Any]:
    """Compact availability summary embedded in a prediction vintage."""
    obs = observations_as_of(
        session, canonical_game_id=canonical_game_id, as_of_at=as_of_at, data_mode=data_mode
    )
    unverified = observations_as_of(
        session, canonical_game_id=canonical_game_id, as_of_at=as_of_at,
        data_mode=data_mode, production_only=False,
    )
    by_state: dict[str, int] = {}
    for o in obs:
        st = classify(o).value
        by_state[st] = by_state.get(st, 0) + 1
    return {
        "verified_observations": len(obs),
        "unverified_present": len(unverified) - len(obs),
        "by_state": by_state,
        "observation_ids": [o.id for o in obs],
        "as_of_at": as_of_at.isoformat(),
    }
