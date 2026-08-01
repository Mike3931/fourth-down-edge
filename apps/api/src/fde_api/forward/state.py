"""Authoritative scheduler state.

`job_outcome` and `domain_state` are authoritative. `status` is
compatibility-only: it exists so pre-split readers keep working, and no
new logic derives anything from it.

The projection below is the single deterministic mapping from outcome to
the legacy column. It is one-way on purpose — `status` has fewer values
than `job_outcome` (SUCCESS and SUCCESS_WITH_WARNINGS both project to
`finished`), so reading back from it would silently lose information.
That asymmetry is exactly why `status` cannot be authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ScheduledJobRun


class Outcome(StrEnum):
    """How the scheduler EXECUTION went.

    Deliberately independent of data quality: the reliability dashboard is
    built on this, so "the job ran fine but the data was thin" must not be
    counted as an execution failure.
    """

    SUCCESS = "SUCCESS"
    SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
    SKIPPED = "SKIPPED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    INTERRUPTED = "INTERRUPTED"
    RUNNING = "RUNNING"


class DomainState(StrEnum):
    """What the DATA looks like, independent of execution.

    A job can execute perfectly and still leave the domain incomplete —
    a missing provider key is the canonical example.
    """

    COMPLETE = "COMPLETE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    STALE = "STALE"
    SUPPRESSED = "SUPPRESSED"
    NO_ELIGIBLE_RECORDS = "NO_ELIGIBLE_RECORDS"

    # Migrated history only. Legacy rows recorded an execution status that
    # does not establish what the DATA looked like — "finished" says the job
    # ran, not that the domain was complete. New runs may never emit this.
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


# States a live handler is permitted to produce. UNKNOWN_LEGACY is excluded:
# it exists solely to represent history we cannot reconstruct.
LIVE_DOMAIN_STATES = frozenset(DomainState) - {DomainState.UNKNOWN_LEGACY}


class IllegalDomainStateError(ValueError):
    """Raised when live code attempts to emit a migration-only state."""




class StateOrigin(StrEnum):
    LIVE = "LIVE"
    MIGRATED_LEGACY = "MIGRATED_LEGACY"


# Canonical, documented projection. Every Outcome has exactly one legacy value.
OUTCOME_TO_LEGACY_STATUS: dict[Outcome, str] = {
    Outcome.RUNNING: "running",
    Outcome.SUCCESS: "finished",
    Outcome.SUCCESS_WITH_WARNINGS: "finished",
    Outcome.SKIPPED: "skipped",
    Outcome.RETRYABLE_FAILURE: "failed",
    Outcome.TERMINAL_FAILURE: "dead_letter",
    Outcome.INTERRUPTED: "interrupted",
}


class InvalidStateError(ValueError):
    """Raised when a state combination violates the authoritative model."""


def project_status(outcome: Outcome | str) -> str:
    """The one deterministic outcome -> legacy status projection."""
    o = Outcome(outcome)
    try:
        return OUTCOME_TO_LEGACY_STATUS[o]
    except KeyError as e:  # pragma: no cover - unreachable while the map is total
        raise InvalidStateError(f"no legacy projection defined for {o}") from e


def validate_state(
    *,
    domain_state: DomainState | str | None,
    state_origin: StateOrigin | str,
) -> None:
    """Service-layer twin of the database CHECK constraint.

    Both exist deliberately: the constraint stops raw SQL and bulk inserts,
    this gives a clear error before a round trip.
    """
    origin = StateOrigin(state_origin)
    if domain_state is None:
        return
    if DomainState(domain_state) is DomainState.UNKNOWN_LEGACY and origin is not StateOrigin.MIGRATED_LEGACY:
        raise InvalidStateError(
            "UNKNOWN_LEGACY may only appear on rows whose state_origin is "
            "MIGRATED_LEGACY; a live run must report an actual domain state"
        )


def apply_state(
    run: ScheduledJobRun,
    *,
    outcome: Outcome | str,
    domain_state: DomainState | str | None = None,
    state_origin: StateOrigin | str | None = None,
) -> ScheduledJobRun:
    """Write authoritative state and project the compatibility column.

    The only sanctioned way to change a run's state, so outcome and
    `status` can never drift apart through normal code paths.
    """
    origin = StateOrigin(state_origin or run.state_origin or StateOrigin.LIVE)
    validate_state(domain_state=domain_state, state_origin=origin)
    o = Outcome(outcome)
    run.job_outcome = o.value
    if domain_state is not None:
        run.domain_state = DomainState(domain_state).value
    run.state_origin = origin.value
    run.status = project_status(o)  # atomic with the authoritative write
    return run


# --------------------------------------------------------------------------- #
# Drift detection and repair
# --------------------------------------------------------------------------- #


@dataclass
class StatusDrift:
    run_id: str
    job_outcome: str | None
    stored_status: str
    expected_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_outcome": self.job_outcome,
            "stored_status": self.stored_status,
            "expected_status": self.expected_status,
        }


def detect_status_drift(session: Session) -> list[StatusDrift]:
    """Rows whose compatibility column disagrees with the authoritative one.

    Only reachable by writing `status` directly — which nothing in the
    application does, so any hit is either an external write or a bug.
    """
    drifts: list[StatusDrift] = []
    rows = session.execute(
        select(ScheduledJobRun.id, ScheduledJobRun.job_outcome, ScheduledJobRun.status)
        .where(ScheduledJobRun.job_outcome.isnot(None))
    ).all()
    for run_id, outcome, status in rows:
        try:
            expected = project_status(outcome)
        except (ValueError, InvalidStateError):
            continue
        if status != expected:
            drifts.append(StatusDrift(run_id, outcome, status, expected))
    return drifts


def repair_status_drift(session: Session, *, dry_run: bool = False) -> dict[str, Any]:
    """Restore `status` from `job_outcome`.

    Repairs the compatibility column only. The authoritative fields are
    never touched — if they were, a corrupted legacy value could rewrite
    the truth it is supposed to shadow.
    """
    drifts = detect_status_drift(session)
    if not dry_run:
        for d in drifts:
            session.execute(
                update(ScheduledJobRun)
                .where(ScheduledJobRun.id == d.run_id)
                .values(status=d.expected_status)
            )
        session.flush()
    return {
        "repaired": 0 if dry_run else len(drifts),
        "detected": len(drifts),
        "dry_run": dry_run,
        "note": "only the compatibility column is written; authoritative fields are untouched",
        "drifts": [d.as_dict() for d in drifts[:50]],
    }


def detect_invalid_origin(session: Session) -> list[str]:
    """Rows claiming UNKNOWN_LEGACY without a migrated origin."""
    return list(
        session.scalars(
            select(ScheduledJobRun.id).where(
                ScheduledJobRun.domain_state == DomainState.UNKNOWN_LEGACY.value,
                ScheduledJobRun.state_origin != StateOrigin.MIGRATED_LEGACY.value,
            )
        )
    )


def reliability_metrics(session: Session) -> dict[str, int]:
    """Execution health. Uses `job_outcome` only, never domain completeness."""
    rows = session.execute(
        select(ScheduledJobRun.job_outcome, ScheduledJobRun.id)
    ).all()
    out: dict[str, int] = {}
    for outcome, _ in rows:
        key = outcome or "UNRECORDED"
        out[key] = out.get(key, 0) + 1
    return out


def completeness_metrics(session: Session) -> dict[str, Any]:
    """Data completeness. Migrated unknowns get their own bucket rather than
    being folded into complete or incomplete."""
    rows = session.execute(select(ScheduledJobRun.domain_state, ScheduledJobRun.id)).all()
    buckets: dict[str, int] = {}
    for domain, _ in rows:
        key = domain or "UNRECORDED"
        buckets[key] = buckets.get(key, 0) + 1
    unknown = buckets.get(DomainState.UNKNOWN_LEGACY.value, 0)
    return {
        "by_state": buckets,
        "migrated_unknown": unknown,
        "note": (
            "UNKNOWN_LEGACY is reported separately; it is neither complete nor "
            "incomplete, because the legacy row never recorded the domain state"
        ),
    }
