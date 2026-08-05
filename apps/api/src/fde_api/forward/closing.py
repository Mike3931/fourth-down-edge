"""Capturing the close as an immutable record that references a snapshot.

The rule that selects the close is fixed in the frozen policy before any
capture happens, and it is recorded with the capture along with its
version. That pairing is the whole point: a close selected under one rule
and one selected under a revised rule are different facts, and without the
version they are indistinguishable.

Three states, all explicit:

  CAPTURED  a snapshot satisfied the rule; the capture references it
  MISSING   nothing satisfied the rule; the capture records why
  CONFLICT  a different snapshot now satisfies the same identity; the
            original stands and the conflict is recorded beside it

Nothing here reads a result, a score, or anything downstream of kickoff.
The rule cannot see the outcome, so the close cannot be chosen because it
flatters one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ClosingCapture, ConsensusSnapshot
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode
from fde_api.forward.ordering import newest, order_by_time_then_id
from fde_api.util import current_code_commit, utc_now

# The version of the selection RULE, not of this module. It changes only
# when the rule itself changes, and a change starts a new capture identity
# rather than restating existing captures.
CLOSING_RULE_VERSION = "close-v1"

CAPTURED = "CAPTURED"
MISSING = "MISSING"
CONFLICT = "CONFLICT"


class ClosingCaptureError(RuntimeError):
    """A capture that would misstate what the close was."""


@dataclass(frozen=True)
class CaptureOutcome:
    """What a capture attempt did, typed rather than inferred."""

    capture: ClosingCapture
    disposition: str  # captured | unchanged | missing | conflict
    requires_review: bool = False

    @property
    def is_conflict(self) -> bool:
        return self.disposition == "conflict"


def select_closing_snapshot(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    kickoff_utc: datetime,
    max_age_before_kickoff_minutes: int,
    data_mode: DataMode,
) -> ConsensusSnapshot | None:
    """The snapshot the rule selects: last eligible at or before kickoff.

    Ordered by (observed_at, id) so two snapshots sharing an instant
    resolve the same way on every run and on every backend. Ordering by
    time alone left a simultaneous pair to the database's discretion.
    """
    window_start = kickoff_utc - timedelta(minutes=max_age_before_kickoff_minutes)
    rows = list(session.scalars(
        select(ConsensusSnapshot).where(
            ConsensusSnapshot.canonical_game_id == canonical_game_id,
            ConsensusSnapshot.market == market,
            ConsensusSnapshot.data_mode == data_mode.value,
            ConsensusSnapshot.observed_at <= kickoff_utc,
            ConsensusSnapshot.observed_at >= window_start,
        )
    ))
    return newest(rows, when=lambda r: r.observed_at, ident=lambda r: r.id)


def existing_captures(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    cohort: Cohort,
    selection: str | None = None,
    rule_version: str = CLOSING_RULE_VERSION,
) -> list[ClosingCapture]:
    """Every capture sharing one identity, oldest first."""
    rows = list(session.scalars(
        select(ClosingCapture).where(
            ClosingCapture.canonical_game_id == canonical_game_id,
            ClosingCapture.market == market,
            ClosingCapture.cohort == cohort.value,
            ClosingCapture.selection_rule_version == rule_version,
        )
    ))
    rows = [r for r in rows if r.selection == selection]
    return order_by_time_then_id(
        rows, when=lambda r: r.created_at, ident=lambda r: r.id)


def authoritative_capture(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    cohort: Cohort,
    selection: str | None = None,
    rule_version: str = CLOSING_RULE_VERSION,
) -> ClosingCapture | None:
    """The capture that counts: the FIRST non-conflict one.

    Deliberately first, not latest. A later conflicting capture must never
    displace the original - if it could, "no overwrite" would be a
    statement about the row rather than about what the system believes.
    """
    for c in existing_captures(
        session, canonical_game_id=canonical_game_id, market=market,
        cohort=cohort, selection=selection, rule_version=rule_version,
    ):
        if c.status != CONFLICT:
            return c
    return None


def capture_close(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    kickoff_utc: datetime,
    selection_rule: str,
    max_age_before_kickoff_minutes: int,
    cohort: Cohort,
    data_mode: DataMode,
    provider_mode: ProviderMode,
    policy_version: str | None,
    scheduled_slot: datetime | None,
    now: datetime | None = None,
    selection: str | None = None,
    rule_version: str = CLOSING_RULE_VERSION,
) -> CaptureOutcome:
    """Record the close for one (game, market, selection) under one rule.

    Never mutates the referenced snapshot, and never rewrites a prior
    capture. A repeat of the same selection is a no-op; a different
    selection is recorded as a conflict requiring review.
    """
    now = now or utc_now()
    snapshot = select_closing_snapshot(
        session,
        canonical_game_id=canonical_game_id,
        market=market,
        kickoff_utc=kickoff_utc,
        max_age_before_kickoff_minutes=max_age_before_kickoff_minutes,
        data_mode=data_mode,
    )
    prior = authoritative_capture(
        session, canonical_game_id=canonical_game_id, market=market,
        cohort=cohort, selection=selection, rule_version=rule_version,
    )

    def _new(status: str, **kw: Any) -> ClosingCapture:
        row = ClosingCapture(
            id=f"cc_{uuid.uuid4().hex[:20]}",
            canonical_game_id=canonical_game_id,
            market=market,
            selection=selection,
            selection_rule=selection_rule,
            selection_rule_version=rule_version,
            scheduled_slot=scheduled_slot,
            captured_at=now,
            cohort=cohort.value,
            data_mode=data_mode.value,
            provider_mode=provider_mode.value,
            policy_version=policy_version,
            code_commit=current_code_commit(),
            status=status,
            created_at=now,
            **kw,
        )
        session.add(row)
        session.flush()
        return row

    if snapshot is None:
        if prior is not None:
            # A close was already recorded. Losing it because a later scan
            # found nothing would delete a fact, so this is a no-op.
            return CaptureOutcome(prior, "unchanged")
        reason = (
            f"no consensus snapshot for {market} within "
            f"{max_age_before_kickoff_minutes} minutes before kickoff "
            f"{kickoff_utc.isoformat()}; CLV is unavailable for this market"
        )
        row = _new(
            MISSING,
            consensus_snapshot_id=None,
            missing_close_reason=reason,
            source_lineage={"rule": selection_rule, "window_minutes":
                            max_age_before_kickoff_minutes},
        )
        return CaptureOutcome(row, "missing")

    lineage = {
        "rule": selection_rule,
        "rule_version": rule_version,
        "window_minutes": max_age_before_kickoff_minutes,
        "kickoff_utc": kickoff_utc.isoformat(),
        "snapshot_observed_at": snapshot.observed_at.isoformat(),
        "snapshot_method_version": snapshot.method_version,
        "snapshot_provider_mode": snapshot.provider_mode,
        "eligible_books": snapshot.eligible_books,
    }

    if prior is None:
        row = _new(
            CAPTURED,
            consensus_snapshot_id=snapshot.id,
            source_lineage=lineage,
        )
        return CaptureOutcome(row, "captured")

    if prior.status == CAPTURED and prior.consensus_snapshot_id == snapshot.id:
        return CaptureOutcome(prior, "unchanged")

    if prior.status == MISSING:
        # A close appeared where none had. That is new information, not a
        # contradiction, so it is recorded as a conflict for review rather
        # than silently upgrading the MISSING record - which would erase
        # the fact that the close was absent when it was first sought.
        reason = (
            f"a close was previously recorded as MISSING ({prior.id}); the rule now "
            f"selects snapshot {snapshot.id}. The original stands; a human must decide "
            "which reflects what was observable at capture time."
        )
    else:
        reason = (
            f"capture {prior.id} references snapshot {prior.consensus_snapshot_id}; "
            f"the rule now selects {snapshot.id}. The original is not overwritten."
        )
    row = _new(
        CONFLICT,
        consensus_snapshot_id=snapshot.id,
        conflict_reason=reason,
        conflicts_with_id=prior.id,
        source_lineage=lineage,
    )
    return CaptureOutcome(row, "conflict", requires_review=True)


def has_unresolved_conflict(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    cohort: Cohort,
    selection: str | None = None,
    rule_version: str = CLOSING_RULE_VERSION,
) -> bool:
    """True when any capture for this identity is a CONFLICT.

    CLV must not be finalised while this holds: a CLV computed against a
    disputed close states a comparison nobody has agreed to.
    """
    return any(
        c.status == CONFLICT
        for c in existing_captures(
            session, canonical_game_id=canonical_game_id, market=market,
            cohort=cohort, selection=selection, rule_version=rule_version,
        )
    )


def closing_snapshot_for(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    cohort: Cohort,
    selection: str | None = None,
    rule_version: str = CLOSING_RULE_VERSION,
) -> ConsensusSnapshot | None:
    """The snapshot the authoritative capture points at, if any.

    Returns None when the close is MISSING or disputed - both of which are
    reasons CLV is unavailable, not reasons to substitute another quote.
    """
    if has_unresolved_conflict(
        session, canonical_game_id=canonical_game_id, market=market,
        cohort=cohort, selection=selection, rule_version=rule_version,
    ):
        return None
    capture = authoritative_capture(
        session, canonical_game_id=canonical_game_id, market=market,
        cohort=cohort, selection=selection, rule_version=rule_version,
    )
    if capture is None or capture.status != CAPTURED:
        return None
    return session.get(ConsensusSnapshot, capture.consensus_snapshot_id)


def describe(capture: ClosingCapture) -> dict[str, Any]:
    return {
        "id": capture.id,
        "canonical_game_id": capture.canonical_game_id,
        "market": capture.market,
        "selection": capture.selection,
        "consensus_snapshot_id": capture.consensus_snapshot_id,
        "selection_rule": capture.selection_rule,
        "selection_rule_version": capture.selection_rule_version,
        "scheduled_slot": capture.scheduled_slot.isoformat() if capture.scheduled_slot else None,
        "captured_at_utc": capture.captured_at.isoformat(),
        "cohort": capture.cohort,
        "data_mode": capture.data_mode,
        "provider_mode": capture.provider_mode,
        "policy_version": capture.policy_version,
        "code_commit": capture.code_commit,
        "status": capture.status,
        "missing_close_reason": capture.missing_close_reason,
        "conflict_reason": capture.conflict_reason,
        "conflicts_with_id": capture.conflicts_with_id,
        "source_lineage": capture.source_lineage,
    }
