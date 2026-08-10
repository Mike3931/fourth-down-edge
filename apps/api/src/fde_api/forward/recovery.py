"""Recovery lineage: chains, typed replay decisions, and effect inspection.

The central question after a crash is not "did the run finish" but "what
did it already do". Scheduler status cannot answer that — a process can
commit a domain write and die before finalizing its own row. So prior
effects are determined by inspecting the DOMAIN tables through the same
idempotency identity the handler used, and the answer is recorded as a
typed `ReplayDecision` rather than free text.

Chain shape: the initial run is its own root at sequence zero; each
recovery references its immediate predecessor, keeps the root and logical
slot, and increments by exactly one. Only one member may be active at a
time, so a chain is a line, never a tree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import (
    AvailabilityAssessment,
    ConsensusSnapshot,
    ForwardLedgerEntry,
    ForwardPrediction,
    InjuryObservation,
    ManualBookPriceEntry,
    OddsQuote,
    ScheduledJobRun,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.forward.cohort import ProviderMode
from fde_api.forward.state import Outcome, StateOrigin

# --------------------------------------------------------------------------- #
# Typed recovery keys
# --------------------------------------------------------------------------- #

# The separator is a control character deliberately: it cannot occur in a job
# name, cohort, or ISO timestamp, so a parsed key can never be confused with
# one that merely contains the literal text.
_KEY_SEP = "|"
_RECOVERY_MARKER = "recovery"

# --- the bound ------------------------------------------------------------ #
# `scheduled_job_runs.idempotency_key` is VARCHAR(160) and `root_run_id` is
# VARCHAR(64). SQLite does not enforce VARCHAR length; PostgreSQL does, and
# rejects the insert with StringDataRightTruncation. So an unbounded key
# generator passes the entire SQLite suite and fails against the real
# database - which is precisely what happened: the params dict was inlined
# into the key, and a fixture odds payload overflowed the column.
#
# The bound is therefore arithmetic rather than assumed. A base key is
# capped so that the WORST-CASE recovery suffix still fits, which makes
# every key in a chain fit by construction rather than by luck.
IDEMPOTENCY_KEY_MAX = 160
_RUN_ID_MAX = 64
_SEQUENCE_DIGITS = 6
_DIGEST_CHARS = 16
# The truncation marker must not be _KEY_SEP, or a bounded base key would
# parse as though it carried a recovery segment.
_TRUNCATION_MARKER = "~"

# "|recovery|" + root_run_id + "|" + sequence
RECOVERY_SUFFIX_MAX = (
    2 * len(_KEY_SEP) + len(_RECOVERY_MARKER) + _RUN_ID_MAX + len(_KEY_SEP) + _SEQUENCE_DIGITS
)
BASE_KEY_MAX = IDEMPOTENCY_KEY_MAX - RECOVERY_SUFFIX_MAX


def bound_key(text: str, limit: int = BASE_KEY_MAX) -> str:
    """Cap a key at `limit` characters without losing distinctness.

    Short keys - which is every key the scheduler generates in practice -
    pass through byte-for-byte, so historical rows keep matching. Only an
    over-long key is rewritten, and it is rewritten deterministically: a
    readable prefix plus a digest of the WHOLE input, so two different
    over-long keys never collapse onto one another.
    """
    if len(text) <= limit:
        return text
    keep = limit - _DIGEST_CHARS - len(_TRUNCATION_MARKER)
    if keep < 1:
        raise RecoveryError(f"key limit {limit} is too small to bound a key distinctly")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    return f"{text[:keep]}{_TRUNCATION_MARKER}{digest}"


@dataclass(frozen=True)
class RecoveryKey:
    """The authoritative identity of one attempt at a logical slot.

    Replaces a free-form `f"{key}#recovery{n}"` suffix. That form was
    unparseable - nothing could recover the root, slot or sequence from it
    without string surgery, and any job name containing the marker would
    have collided. Worse, it encouraged prefix matching (`LIKE 'key%'`),
    which silently matches unrelated slots whose keys share a prefix.

    Sequence 0 IS the original key, unchanged, so historical rows written
    before this type existed remain readable and match exactly.
    """

    job_name: str
    cohort: str
    logical_slot: str
    root_run_id: str
    sequence: int
    original_key: str

    def render(self) -> str:
        if self.sequence == 0:
            return self.original_key
        return _KEY_SEP.join([
            self.original_key,
            _RECOVERY_MARKER,
            self.root_run_id,
            str(self.sequence),
        ])

    @classmethod
    def parse(cls, rendered: str) -> tuple[str, str | None, int]:
        """Return (original_key, root_run_id, sequence).

        A key with no recovery segment is sequence 0 and its own original.
        """
        parts = rendered.split(_KEY_SEP)
        if len(parts) < 4 or parts[-3] != _RECOVERY_MARKER:
            return rendered, None, 0
        return _KEY_SEP.join(parts[:-3]), parts[-2], int(parts[-1])


def build_recovery_key(
    *,
    job_name: str,
    cohort: str,
    logical_slot: str,
    root_run_id: str,
    sequence: int,
    original_key: str,
) -> str:
    """Render the authoritative key for a recovery attempt.

    Refuses rather than truncates when the result would not fit the column.
    Truncating here would silently merge two distinct attempts onto one
    unique key, which is worse than a failed insert: the second attempt
    would be treated as already done. Given `bound_key` caps the base key at
    `BASE_KEY_MAX`, the refusal is unreachable for scheduler-generated keys
    and only fires on a hand-supplied original.
    """
    if sequence < 0:
        raise RecoveryError(f"recovery sequence may not be negative, got {sequence}")
    if len(str(sequence)) > _SEQUENCE_DIGITS:
        raise RecoveryError(f"recovery sequence {sequence} exceeds {_SEQUENCE_DIGITS} digits")
    rendered = RecoveryKey(
        job_name=job_name,
        cohort=cohort,
        logical_slot=logical_slot,
        root_run_id=root_run_id,
        sequence=sequence,
        original_key=original_key,
    ).render()
    if len(rendered) > IDEMPOTENCY_KEY_MAX:
        raise RecoveryError(
            f"recovery key would be {len(rendered)} characters, exceeding the "
            f"{IDEMPOTENCY_KEY_MAX}-character column; the base key was not bounded"
        )
    return rendered


def is_legacy_recovery_key(rendered: str) -> bool:
    """True for the old free-form suffix. Readable, never generated."""
    return "#recovery" in rendered


class ReplayDecision(StrEnum):
    """Authoritative. Free text may explain it but never replaces it."""

    NO_PRIOR_EFFECTS_REPLAY = "NO_PRIOR_EFFECTS_REPLAY"
    PRIOR_EFFECTS_IDEMPOTENT_REPLAY = "PRIOR_EFFECTS_IDEMPOTENT_REPLAY"
    PRIOR_EFFECTS_NO_REPLAY = "PRIOR_EFFECTS_NO_REPLAY"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class JobCategory(StrEnum):
    OBSERVATION = "OBSERVATION"
    SNAPSHOT = "SNAPSHOT"
    TERMINAL = "TERMINAL"


JOB_CATEGORIES: dict[str, JobCategory] = {
    "schedule_refresh": JobCategory.OBSERVATION,
    "odds_capture": JobCategory.OBSERVATION,
    "weather_capture": JobCategory.OBSERVATION,
    "injury_reconciliation": JobCategory.OBSERVATION,
    "consensus_build": JobCategory.SNAPSHOT,
    "availability_computation": JobCategory.SNAPSHOT,
    "feature_snapshot": JobCategory.SNAPSHOT,
    "prediction_vintage": JobCategory.SNAPSHOT,
    # A price is an observation with immutable identity: entering the same
    # price twice is deduplicated, not duplicated.
    "price_observation": JobCategory.OBSERVATION,
    # An evaluation is a SNAPSHOT: its identity is (game, market, selection,
    # slot, policy, model), so regenerating one slot reproduces it exactly.
    "price_evaluation": JobCategory.SNAPSHOT,
    "closing_capture": JobCategory.TERMINAL,
    "result_ingestion": JobCategory.TERMINAL,
    "settlement": JobCategory.TERMINAL,
    "forward_evaluation": JobCategory.TERMINAL,
}

class Attribution(StrEnum):
    """How effects are tied back to the run that produced them.

    This is the crux of effect inspection: "are there rows in the table" is
    not the question. The question is "are there rows THIS run wrote". The
    original implementation counted the whole table for every job except
    `odds_capture`, so any pre-existing row - one from a different slot, a
    different game, or a different cohort - was read as this run's effects.
    For an observation job that only mislabelled the reason. For a TERMINAL
    job it changed behaviour: one unrelated settled ledger entry anywhere in
    the database made every settlement recovery conclude
    PRIOR_EFFECTS_NO_REPLAY, so settlement was skipped and silently never
    happened.
    """

    REQUEST_ID = "REQUEST_ID"  # the row records the originating request
    EXACT_SLOT = "EXACT_SLOT"  # the row's as-of instant IS the logical slot
    SLOT_WINDOW = "SLOT_WINDOW"  # captured at some point during the slot
    SCOPE_REQUIRED = "SCOPE_REQUIRED"  # not attributable without an explicit scope


@dataclass(frozen=True)
class EffectSource:
    """Where a job's committed effects live and how to attribute them."""

    table: Any
    time_column: str | None
    attribution: Attribution
    request_column: str | None = None


# Domain table each job writes, and the rule for attributing rows to a run.
#
# SNAPSHOT jobs stamp the cutoff itself, so attribution is exact equality.
# OBSERVATION jobs stamp the moment of capture, which lands somewhere inside
# the slot rather than on it, so attribution is a half-open window.
# TERMINAL jobs are not attributable from a slot at all - a settlement is
# about a game, not a time - so they demand an explicit scope and refuse to
# guess without one.
_EFFECT_SOURCES: dict[str, EffectSource] = {
    "schedule_refresh": EffectSource(
        ScheduleObservation, "observed_at", Attribution.SLOT_WINDOW),
    "odds_capture": EffectSource(
        OddsQuote, "observed_at", Attribution.REQUEST_ID, request_column="request_id"),
    "weather_capture": EffectSource(
        WeatherForecastVintage, "observed_at", Attribution.SLOT_WINDOW),
    "injury_reconciliation": EffectSource(
        InjuryObservation, "observed_at", Attribution.SLOT_WINDOW),
    "consensus_build": EffectSource(
        ConsensusSnapshot, "observed_at", Attribution.EXACT_SLOT),
    "availability_computation": EffectSource(
        AvailabilityAssessment, "as_of_at", Attribution.EXACT_SLOT),
    "prediction_vintage": EffectSource(
        ForwardPrediction, "as_of_at", Attribution.EXACT_SLOT),
    # entered_at, not observed_at: observed_at is when the USER saw the
    # price, which can precede the slot by hours. What attributes the row to
    # this run is when the run WROTE it.
    "price_observation": EffectSource(
        ManualBookPriceEntry, "entered_at", Attribution.SLOT_WINDOW),
    "price_evaluation": EffectSource(
        ForwardLedgerEntry, "as_of_at", Attribution.EXACT_SLOT),
    "settlement": EffectSource(
        ForwardLedgerEntry, "settled_at", Attribution.SCOPE_REQUIRED),
    "forward_evaluation": EffectSource(
        ForwardLedgerEntry, "settled_at", Attribution.SCOPE_REQUIRED),
}

# Backwards-compatible view for callers that only need the table.
_EFFECT_TABLES: dict[str, Any] = {k: v.table for k, v in _EFFECT_SOURCES.items()}

# How long after its logical slot an observation job's writes are still
# attributable to that slot. Deliberately explicit rather than unbounded: a
# window wide enough to swallow the next slot would attribute the successor's
# rows to this run. The scheduler passes the job's own interval when it has
# one, which is the tightest correct bound.
DEFAULT_ATTRIBUTION_WINDOW = timedelta(hours=1)


class RecoveryError(RuntimeError):
    """Raised when a recovery would violate a chain invariant."""


@dataclass
class EffectInspection:
    """What a handler already did, determined from the domain, not the run."""

    job_kind: str
    category: JobCategory
    record_type: str
    record_identifiers: list[str] = field(default_factory=list)
    idempotency_keys: list[str] = field(default_factory=list)
    expected_effect_count: int | None = None
    actual_effect_count: int = 0
    complete: bool | None = None
    conflicts: list[str] = field(default_factory=list)
    recommended_decision: ReplayDecision = ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
    reason: str = ""
    # How the count was scoped. Recorded because a count is only meaningful
    # alongside the rule that produced it, and an audit reading the lineage
    # later must be able to tell a scoped count from a table census.
    attribution: Attribution = Attribution.SCOPE_REQUIRED
    attribution_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_kind": self.job_kind,
            "category": self.category.value,
            "record_type": self.record_type,
            "record_identifiers": self.record_identifiers[:50],
            "idempotency_keys": self.idempotency_keys[:50],
            "expected_effect_count": self.expected_effect_count,
            "actual_effect_count": self.actual_effect_count,
            "complete": self.complete,
            "conflicts": self.conflicts,
            "recommended_decision": self.recommended_decision.value,
            "reason": self.reason,
            "attribution": self.attribution.value,
            "attribution_detail": self.attribution_detail,
        }


def inspect_prior_effects(
    session: Session,
    *,
    job_kind: str,
    idempotency_key: str,
    logical_slot: datetime | None = None,
    data_mode: str | None = None,
    canonical_game_id: str | None = None,
    attribution_window: timedelta | None = None,
) -> EffectInspection:
    """Determine whether committed domain effects exist FOR THIS RUN.

    Deliberately reads the DOMAIN, never the scheduler run status: a
    process that committed its write and then died leaves a `running` row
    and real records, and only the latter is evidence.

    Every query is scoped by the run's attribution rule (see `Attribution`)
    and, where the table carries one, by `data_mode`. Without that scoping
    the count is a table census, and a census answers a different question
    than the one recovery asks.
    """
    category = JOB_CATEGORIES.get(job_kind, JobCategory.OBSERVATION)
    source = _EFFECT_SOURCES.get(job_kind)
    if source is None:
        # Three jobs are categorised in JOB_CATEGORIES but have no entry in
        # _EFFECT_SOURCES: feature_snapshot (SNAPSHOT), closing_capture and
        # result_ingestion (both TERMINAL). They do write domain tables —
        # ClosingCapture and a FINAL ScheduleObservation respectively — so
        # the old reason here, "job writes no tracked domain table", was
        # simply false for them, and they never reached the
        # SCOPE_REQUIRED branch that exists so a TERMINAL job refuses to
        # guess.
        #
        # Replay is nonetheless safe for both TODAY, because both handlers
        # are idempotent by construction: a ClosingCapture is immutable
        # with a uniqueness constraint, and `ingest_result` refuses a
        # correction to an existing final. The DECISION is right; what was
        # wrong was the stated reason, and the fact that the safety rests
        # on those handlers happening to be idempotent rather than on this
        # function determining anything.
        #
        # Registering them would make a scheduled (paramless) recovery of
        # either one demand a canonical_game_id and otherwise block on
        # MANUAL_REVIEW_REQUIRED. That is what this module's stated
        # principle implies, and it is an availability decision — it would
        # stop recoveries that currently complete safely — so it is
        # recorded rather than taken here.
        untracked = job_kind not in JOB_CATEGORIES
        return EffectInspection(
            job_kind=job_kind, category=category, record_type="unknown",
            recommended_decision=ReplayDecision.NO_PRIOR_EFFECTS_REPLAY,
            reason=(
                "job is not categorised and writes no tracked domain table; "
                "replay is safe"
                if untracked
                else (
                    f"{job_kind} is categorised {category.value} but has no registered "
                    "effect source, so prior effects were NOT inspected; replay rests on "
                    "the handler being idempotent, not on evidence"
                )
            ),
            attribution_detail="no effect source registered for this job",
        )

    table = source.table
    insp = EffectInspection(job_kind=job_kind, category=category, record_type=table.__tablename__)
    insp.idempotency_keys = [idempotency_key]
    insp.attribution = source.attribution

    stmt = select(table.id)
    scoped = False

    if source.attribution is Attribution.REQUEST_ID and source.request_column:
        stmt = stmt.where(getattr(table, source.request_column) == idempotency_key)
        scoped = True
        insp.attribution_detail = f"{source.request_column} == the chain's original key"

    elif (
        source.attribution is Attribution.EXACT_SLOT
        and logical_slot is not None
        and source.time_column
    ):
        stmt = stmt.where(getattr(table, source.time_column) == logical_slot)
        scoped = True
        insp.attribution_detail = f"{source.time_column} == {logical_slot.isoformat()}"

    elif (
        source.attribution is Attribution.SLOT_WINDOW
        and logical_slot is not None
        and source.time_column
    ):
        window = attribution_window or DEFAULT_ATTRIBUTION_WINDOW
        col = getattr(table, source.time_column)
        stmt = stmt.where(col >= logical_slot, col < logical_slot + window)
        scoped = True
        insp.attribution_detail = (
            f"{source.time_column} in [{logical_slot.isoformat()}, +{window})"
        )

    elif source.attribution is Attribution.SCOPE_REQUIRED:
        if canonical_game_id is None:
            # Refusing beats guessing. A terminal job's effects cannot be
            # derived from a timestamp, and treating unrelated rows as this
            # run's effects would silently skip a settlement.
            insp.complete = None
            insp.recommended_decision = ReplayDecision.MANUAL_REVIEW_REQUIRED
            insp.reason = (
                f"{job_kind} is a terminal job whose effects are attributable only by "
                "game; no canonical_game_id was supplied, so prior effects cannot be "
                "determined and a human must decide"
            )
            insp.attribution_detail = "no scope supplied"
            return insp
        stmt = stmt.where(table.canonical_game_id == canonical_game_id)
        scoped = True
        insp.attribution_detail = f"canonical_game_id == {canonical_game_id}"

    if not scoped:
        # Attribution was impossible (no slot on the predecessor row). For
        # idempotent categories replaying is the safe answer; the reason
        # records that the conclusion rests on absence of evidence.
        insp.complete = False
        insp.recommended_decision = ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
        insp.reason = (
            "the predecessor carries no logical slot, so effects could not be "
            "attributed; replaying an idempotent handler is the safe default"
        )
        insp.attribution_detail = "unattributable: no logical slot"
        return insp

    if data_mode is not None and hasattr(table, "data_mode"):
        stmt = stmt.where(table.data_mode == data_mode)
        insp.attribution_detail += f", data_mode == {data_mode}"

    ids = list(session.scalars(stmt))
    insp.record_identifiers = [str(i) for i in ids]
    insp.actual_effect_count = len(ids)
    return _decide(insp)


def _decide(insp: EffectInspection) -> EffectInspection:
    """Map an inspection to a typed decision, per category rules."""
    if insp.actual_effect_count == 0:
        insp.complete = False
        insp.recommended_decision = ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
        insp.reason = "no committed domain effects found; run the handler normally"
        return insp

    if insp.conflicts:
        insp.complete = None
        insp.recommended_decision = ReplayDecision.MANUAL_REVIEW_REQUIRED
        insp.reason = f"conflicting effects detected: {'; '.join(insp.conflicts[:3])}"
        return insp

    if insp.category is JobCategory.OBSERVATION:
        # Observation identity (content hash / quote identity) makes a repeat
        # capture a no-op, so replay cannot duplicate.
        insp.complete = True
        insp.recommended_decision = ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY
        insp.reason = (
            "observations carry immutable identity, so replay is deduplicated "
            "rather than duplicated"
        )
        return insp

    if insp.category is JobCategory.SNAPSHOT:
        # Snapshot identity includes cutoff, cohort, policy, and model version,
        # so regenerating the same vintage is a verified no-op.
        insp.complete = True
        insp.recommended_decision = ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY
        insp.reason = (
            "snapshot identity includes cutoff, cohort, policy and model version; "
            "regeneration is verified against the stored artifact hash"
        )
        return insp

    # Terminal jobs are never blindly replayed.
    insp.complete = True
    insp.recommended_decision = ReplayDecision.PRIOR_EFFECTS_NO_REPLAY
    insp.reason = (
        "terminal effect already recorded; finalize the recovery without "
        "repeating settlement or evaluation"
    )
    return insp


def inspect_terminal_effects(
    session: Session,
    *,
    job_kind: str,
    canonical_game_id: str,
    expected_result: str | None = None,
) -> EffectInspection:
    """Terminal-job inspection with conflict detection.

    Settlement must distinguish "no result", "matching result", and
    "conflicting result" — the last requires a human.
    """
    insp = EffectInspection(
        job_kind=job_kind, category=JobCategory.TERMINAL,
        record_type=ForwardLedgerEntry.__tablename__,
    )
    rows = list(session.scalars(
        select(ForwardLedgerEntry).where(
            ForwardLedgerEntry.canonical_game_id == canonical_game_id,
            ForwardLedgerEntry.result.isnot(None),
        )
    ))
    insp.record_identifiers = [str(r.id) for r in rows]
    insp.actual_effect_count = len(rows)

    if not rows:
        return _decide(insp)
    if expected_result is not None:
        mismatched = [r for r in rows if r.result != expected_result]
        if mismatched:
            insp.conflicts = [
                f"entry {r.id} settled {r.result}, expected {expected_result}" for r in mismatched
            ]
    return _decide(insp)


# --------------------------------------------------------------------------- #
# Chain construction and invariants
# --------------------------------------------------------------------------- #

# Outcomes that close a chain; nothing may follow them automatically.
_CLOSING_OUTCOMES = {Outcome.SUCCESS.value, Outcome.SUCCESS_WITH_WARNINGS.value,
                     Outcome.SKIPPED.value}
_TERMINAL_OUTCOME = Outcome.TERMINAL_FAILURE.value


def chain_members(session: Session, root_run_id: str) -> list[ScheduledJobRun]:
    """Chain in chronological order."""
    return list(session.scalars(
        select(ScheduledJobRun)
        .where(ScheduledJobRun.root_run_id == root_run_id)
        .order_by(ScheduledJobRun.recovery_sequence, ScheduledJobRun.created_at)
    ))


def active_member(session: Session, root_run_id: str) -> ScheduledJobRun | None:
    """The one member currently permitted to execute, if any."""
    for m in chain_members(session, root_run_id):
        if m.job_outcome == Outcome.RUNNING.value:
            return m
    return None


def validate_recovery(
    predecessor: ScheduledJobRun,
    *,
    cohort: str | None,
    policy_version: str | None,
    logical_slot: datetime | None,
    provider_mode: str | None,
    reason: str,
    administrative_override: bool = False,
    override_operator: str | None = None,
    override_reason: str | None = None,
) -> None:
    """Service-level enforcement of every chain invariant."""
    if not reason or not reason.strip():
        raise RecoveryError("a recovery requires a reason")

    if predecessor.job_outcome in _CLOSING_OUTCOMES:
        raise RecoveryError(
            f"run {predecessor.id} closed the chain with {predecessor.job_outcome}; "
            "a completed chain cannot be silently reopened"
        )

    if predecessor.job_outcome == _TERMINAL_OUTCOME and not administrative_override:
        raise RecoveryError(
            f"run {predecessor.id} failed terminally; automatic recovery is refused. "
            "An administrative override with operator and reason is required."
        )

    if administrative_override:
        if not override_operator or not override_operator.strip():
            raise RecoveryError("administrative override requires an operator")
        if not override_reason or not override_reason.strip():
            raise RecoveryError("administrative override requires a reason")

    prior_slot = predecessor.logical_slot or predecessor.scheduled_for
    if logical_slot is not None and prior_slot is not None and logical_slot != prior_slot:
        raise RecoveryError("a recovery may not change the logical slot")
    if cohort is not None and predecessor.data_mode is not None and cohort != predecessor.data_mode:
        # data_mode is the persisted cohort axis on the run record.
        raise RecoveryError("a recovery may not change the cohort")
    if policy_version is not None:
        stored = (predecessor.error_summary or "")
        if "policy=" in stored and f"policy={policy_version}" not in stored:
            raise RecoveryError("a recovery may not change the policy version")
    # Provider-mode invariance. This used to be a comment explaining why the
    # check could not exist: `scheduled_job_runs` did not persist the mode,
    # so there was nothing to compare against, and a check that cannot fail
    # is worse than none. Migration e7b3c04d1f28 added the column, so the
    # check is now real.
    #
    # It matters because a recovery that changes the mode launders data
    # across the fixture/live boundary: a slot captured from a fixture
    # payload, recovered under LIVE, would produce records stamped LIVE that
    # no provider ever returned.
    stored_mode = getattr(predecessor, "provider_mode", None)
    if provider_mode is not None and stored_mode:
        if stored_mode == ProviderMode.UNKNOWN_LEGACY.value:
            # A backfilled row. Its true mode is unrecoverable, so the
            # invariant cannot be checked - and an unverifiable invariant is
            # a reason to involve a human, not to wave the recovery through.
            if not administrative_override:
                raise RecoveryError(
                    f"run {predecessor.id} predates provider-mode recording "
                    f"(UNKNOWN_LEGACY), so mode invariance cannot be verified; "
                    "an administrative override with operator and reason is required"
                )
        elif provider_mode != stored_mode:
            raise RecoveryError(
                f"a recovery may not change the provider mode: run {predecessor.id} "
                f"ran under {stored_mode}, this recovery would run under {provider_mode}"
            )


def build_recovery_lineage(
    session: Session,
    predecessor: ScheduledJobRun,
    *,
    run_id: str,
    recovery_key: str,
    reason: str,
    now: datetime,
    provider_mode: str | None = None,
    inspection: EffectInspection | None = None,
    administrative_override: bool = False,
    override_operator: str | None = None,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Lineage payload for a recovery run.

    Refuses to create a parallel branch: if another member of the chain is
    still active, this recovery is not permitted.
    """
    root = predecessor.root_run_id or predecessor.id
    if active_member(session, root) is not None and not administrative_override:
        raise RecoveryError(
            f"chain {root} already has an active member; parallel recovery "
            "branches are prohibited"
        )
    validate_recovery(
        predecessor,
        cohort=predecessor.data_mode,
        policy_version=None,
        logical_slot=predecessor.logical_slot or predecessor.scheduled_for,
        provider_mode=provider_mode,
        reason=reason,
        administrative_override=administrative_override,
        override_operator=override_operator,
        override_reason=override_reason,
    )
    decision = inspection.recommended_decision if inspection else None
    return {
        "root_run_id": root,
        "recovery_of_run_id": predecessor.id,
        "recovery_sequence": (predecessor.recovery_sequence or 0) + 1,
        "logical_slot": predecessor.logical_slot or predecessor.scheduled_for,
        "original_idempotency_key": predecessor.original_idempotency_key
        or predecessor.idempotency_key,
        "idempotency_key": recovery_key,
        "recovery_reason": reason,
        "reconciled_at": now,
        "prior_effects_detected": bool(inspection and inspection.actual_effect_count > 0),
        "replay_decision": decision.value if decision else None,
        "administrative_override": administrative_override,
        "override_operator": override_operator,
        "override_reason": override_reason,
        # A recovery is a LIVE run even when recovering migrated history.
        "state_origin": StateOrigin.LIVE.value,
    }


# --------------------------------------------------------------------------- #
# Lineage integrity checks (consumed by Data Health)
# --------------------------------------------------------------------------- #


def lineage_violations(session: Session) -> list[dict[str, Any]]:
    """Structural problems in persisted recovery chains."""
    problems: list[dict[str, Any]] = []
    runs = list(session.scalars(select(ScheduledJobRun)))
    by_id = {r.id: r for r in runs}
    by_root: dict[str, list[ScheduledJobRun]] = {}
    for r in runs:
        by_root.setdefault(r.root_run_id or r.id, []).append(r)

    for root, members in by_root.items():
        members.sort(key=lambda m: (m.recovery_sequence or 0, m.created_at))

        active = [m for m in members if m.job_outcome == Outcome.RUNNING.value]
        if len(active) > 1:
            problems.append({"check": "multiple_active_runs", "root": root,
                             "detail": [m.id for m in active], "severity": "CRITICAL"})

        seqs = [m.recovery_sequence or 0 for m in members]
        if seqs and seqs[0] != 0:
            problems.append({"check": "root_sequence_not_zero", "root": root,
                             "detail": seqs, "severity": "CRITICAL"})
        if len(seqs) != len(set(seqs)):
            problems.append({"check": "recovery_branch", "root": root,
                             "detail": seqs, "severity": "CRITICAL"})
        for i in range(1, len(seqs)):
            if seqs[i] != seqs[i - 1] + 1:
                problems.append({"check": "incorrect_recovery_sequence", "root": root,
                                 "detail": seqs, "severity": "CRITICAL"})
                break

        for m in members:
            is_root = (m.recovery_sequence or 0) == 0
            # Predecessor checks apply to recoveries only; the remaining
            # checks apply to every member, including the root.
            if is_root:
                pass
            elif not m.recovery_of_run_id:
                problems.append({"check": "broken_predecessor_link", "root": root,
                                 "detail": m.id, "severity": "CRITICAL"})
            elif m.recovery_of_run_id not in by_id:
                problems.append({"check": "broken_predecessor_link", "root": root,
                                 "detail": f"{m.id} -> missing {m.recovery_of_run_id}",
                                 "severity": "CRITICAL"})
            elif (by_id[m.recovery_of_run_id].root_run_id or m.recovery_of_run_id) != root:
                problems.append({"check": "root_mismatch", "root": root,
                                 "detail": m.id, "severity": "CRITICAL"})
            if not is_root and not m.recovery_reason:
                problems.append({"check": "missing_recovery_reason", "root": root,
                                 "detail": m.id, "severity": "WARNING"})
            if m.administrative_override and not (m.override_operator and m.override_reason):
                problems.append({"check": "missing_override_identity", "root": root,
                                 "detail": m.id, "severity": "CRITICAL"})
            if m.replay_decision is None and m.job_outcome not in (Outcome.RUNNING.value, None):
                problems.append({"check": "replay_decision_absent", "root": root,
                                 "detail": m.id, "severity": "WARNING"})
            if m.replay_decision == ReplayDecision.MANUAL_REVIEW_REQUIRED.value and (
                m.job_outcome == Outcome.RUNNING.value
            ):
                problems.append({"check": "manual_review_unresolved", "root": root,
                                 "detail": m.id, "severity": "CRITICAL"})

        # Every member of a chain ran under one provider mode. `validate_recovery`
        # refuses a mode change at the moment of recovery; this is the detection
        # side, for chains written before that check existed or by any path that
        # bypassed it. A mixed chain means fixture and live records share a
        # logical slot, which no downstream consumer can untangle.
        #
        # UNKNOWN_LEGACY is excluded from the comparison rather than treated as
        # a mismatch: it means "not recorded", so a chain of backfilled rows is
        # unverifiable, not wrong.
        modes = {
            m.provider_mode for m in members
            if m.provider_mode and m.provider_mode != ProviderMode.UNKNOWN_LEGACY.value
        }
        if len(modes) > 1:
            problems.append({"check": "provider_mode_changed_mid_chain", "root": root,
                             "detail": sorted(modes), "severity": "CRITICAL"})

        # A closed chain must not have anything after it.
        for i, m in enumerate(members[:-1]):
            if m.job_outcome in _CLOSING_OUTCOMES:
                problems.append({"check": "closed_chain_reopened", "root": root,
                                 "detail": f"{m.id} closed but {members[i + 1].id} follows",
                                 "severity": "CRITICAL"})
                break
    return problems


def blocks_automatic_recovery(problems: list[dict[str, Any]]) -> bool:
    """Critical lineage corruption requires administrative review."""
    return any(p["severity"] == "CRITICAL" for p in problems)
