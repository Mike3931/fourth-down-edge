"""Logical identity and content, separated, for four domain records.

Two different questions were previously answered by one thing:

  WHICH record is this?   the logical identity - the slot an attempt is
                          aiming at
  WHAT does it say?       the content - what was actually observed,
                          computed or decided

Keeping them apart is what makes "retry" and "conflict" distinguishable. A
second attempt at the same slot carrying the same content is a retry and
must be a no-op. A second attempt at the same slot carrying DIFFERENT
content is a contradiction, and the two are not the same event even though
both look like "the row already exists".

The service-level pre-check that existed before could not tell them apart
under contention: two callers both SELECT, both find nothing, both INSERT.
The database constraint on the logical identity is what actually decides,
and everything here exists to give that constraint something stable to
enforce.

Getting the breadth of the logical identity right is the whole design:

  too broad   every conflicting payload becomes a "new version", and
              contradictions are silently accepted as history
  too narrow  a legitimately revised observation collides with the one it
              revises, and real new information is rejected as a conflict

Each entity's choice is argued in its own section below.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# Bump either version when the FIELD SET or the rendering changes. A stored
# hash is uninterpretable without the version that produced it, which is
# why both are persisted beside the hash rather than assumed.
LOGICAL_IDENTITY_VERSION = "domain-logical-identity-v1"
CONTENT_HASH_VERSION = "domain-content-v1"

# Decimal places for float normalisation. Two paths can compute the same
# quantity through different arithmetic and differ in the last bits; that is
# not a different observation. Six places is far finer than any decision
# this system makes and far coarser than float noise.
_PRECISION = 6

# Distinguishes "the field is absent" from "the field is null". Collapsing
# them would make a record that never carried a value hash the same as one
# that explicitly carries none.
_MISSING = "\x00__MISSING__"


class IdentityError(ValueError):
    """An identity or content payload that cannot be hashed meaningfully."""


class UnknownHashVersion(ValueError):
    """A stored hash carries a version this code does not implement."""


def _norm(value: Any) -> Any:
    """Canonical rendering of one value.

    Timestamps become UTC ISO strings, so two backends returning the same
    instant with different tzinfo objects agree. Floats are rounded. Enums
    render as their stable code, never their Python repr.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # A naive timestamp is a defect elsewhere. Rendering it
            # distinctly exposes it rather than silently absorbing it into
            # an otherwise-matching hash.
            raise IdentityError(
                f"naive timestamp {value.isoformat()} cannot be canonicalised; "
                "identity depends on an unambiguous instant"
            )
        return value.astimezone(UTC).isoformat()
    if isinstance(value, float | int):
        return round(float(value), _PRECISION)
    if isinstance(value, list | tuple):
        return [_norm(v) for v in value]
    if isinstance(value, set | frozenset):
        # Order is not semantic for a set, so it is imposed deterministically.
        return sorted(_norm(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in sorted(value.items())}
    return str(value)


def canonical_payload(fields: dict[str, Any], allowlist: tuple[str, ...]) -> dict[str, Any]:
    """Project a record onto its allowlisted fields, canonically rendered.

    An ALLOWLIST rather than a denylist. A new column added to a table must
    not silently join an identity or a content hash - that would change
    every stored hash without anybody deciding it should.
    """
    unknown = sorted(set(fields) - set(allowlist))
    if unknown:
        raise IdentityError(
            f"fields not in the allowlist reached canonicalisation: {unknown}. "
            "Add them deliberately and bump the version, or drop them."
        )
    return {name: _norm(fields.get(name, _MISSING)) for name in sorted(allowlist)}


def digest(payload: dict[str, Any], *, version: str) -> str:
    """SHA-256 over versioned canonical JSON."""
    body = {"version": version, "fields": payload}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #


class IdentityOutcome(StrEnum):
    """What a create-or-retrieve attempt actually did."""

    CREATED = "CREATED"
    EXISTING_IDENTICAL = "EXISTING_IDENTICAL"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class IdentityResult:
    """The authoritative record and how the caller came to hold it.

    `record` is ALWAYS the authoritative row - on a conflict it is the one
    that was already there, never the rejected attempt. A caller that reads
    `record` without checking `outcome` therefore still sees the truth.
    """

    outcome: IdentityOutcome
    record: Any
    logical_identity_hash: str
    content_hash: str
    existing_content_hash: str | None = None
    conflict_fields: tuple[str, ...] = ()
    requires_manual_review: bool = False

    @property
    def created(self) -> bool:
        return self.outcome is IdentityOutcome.CREATED

    @property
    def conflicted(self) -> bool:
        return self.outcome is IdentityOutcome.CONFLICT

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "logical_identity_hash": self.logical_identity_hash,
            "content_hash": self.content_hash,
            "existing_content_hash": self.existing_content_hash,
            "conflict_fields": list(self.conflict_fields),
            "requires_manual_review": self.requires_manual_review,
        }


@dataclass(frozen=True)
class EntityIdentity:
    """One entity's two field sets and the versions that produced them."""

    entity: str
    logical_fields: tuple[str, ...]
    content_fields: tuple[str, ...]
    # True when a conflict on this entity changes something downstream
    # consumers treat as truth, and therefore needs a human.
    conflict_requires_review: bool
    rationale: str

    def logical_hash(self, values: dict[str, Any]) -> str:
        return digest(
            canonical_payload(values, self.logical_fields),
            version=LOGICAL_IDENTITY_VERSION,
        )

    def content_hash(self, values: dict[str, Any]) -> str:
        return digest(
            canonical_payload(values, self.content_fields),
            version=CONTENT_HASH_VERSION,
        )

    def as_spec(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "logical_identity_version": LOGICAL_IDENTITY_VERSION,
            "logical_identity_fields": list(self.logical_fields),
            "content_hash_version": CONTENT_HASH_VERSION,
            "content_fields": list(self.content_fields),
            "conflict_requires_manual_review": self.conflict_requires_review,
            "rationale": self.rationale,
        }


# --------------------------------------------------------------------------- #
# The four identities
# --------------------------------------------------------------------------- #

CONSENSUS = EntityIdentity(
    entity="consensus_snapshot",
    logical_fields=(
        "canonical_game_id",
        "market",
        "cutoff",
        "cohort",
        "method_version",
    ),
    content_fields=(
        "median_line",
        "home_price_american",
        "away_price_american",
        "over_price_american",
        "under_price_american",
        "no_vig_home_prob",
        "no_vig_over_prob",
        "eligible_books",
        "quote_lineage",
        "line_dispersion",
        "price_dispersion",
        "provider_mode",
    ),
    conflict_requires_review=True,
    rationale=(
        "A consensus is a calculation over the quotes visible at a cutoff. "
        "The slot is (game, market, cutoff, cohort, method): recomputing it "
        "must reproduce it exactly. A LATER cutoff is a new slot and a "
        "legitimate new version; a different method version likewise. A "
        "different answer at the SAME slot means the inputs changed after "
        "the fact, which downstream consumers read as truth - so it needs a "
        "human rather than a silent second row."
    ),
)

EVALUATION = EntityIdentity(
    entity="research_evaluation",
    logical_fields=(
        "canonical_game_id",
        "prediction_identity",
        "price_identity",
        "evaluation_type",
        "cohort",
        "policy_version",
        "model_version",
        "decision_context_hash",
    ),
    content_fields=(
        "status",
        "model_probability",
        "conservative_probability",
        "break_even_probability",
        "expected_value",
        "decision_reason_codes",
        "suppressed",
        "data_completeness",
        "execution_eligible",
    ),
    conflict_requires_review=True,
    rationale=(
        "The decision-context hash is IN the logical identity deliberately. "
        "A health remediation legitimately changes the context, and the "
        "evaluation made afterwards is a genuinely new immutable record - "
        "not a correction of the suppressed one, which stays exactly as it "
        "was. Without the context in the identity, remediation would look "
        "like a conflict; with it, the same context producing a different "
        "analytical answer is a conflict, which is the case that matters."
    ),
)

AVAILABILITY = EntityIdentity(
    entity="availability_assessment",
    logical_fields=(
        "canonical_game_id",
        "player_id",
        "cutoff",
        "cohort",
        "method_version",
    ),
    content_fields=(
        "state",
        "active_prob_low",
        "active_prob_high",
        "snap_share_low",
        "snap_share_high",
        "confidence_tier",
        "is_starting_qb",
        "observation_lineage",
        "missing_data",
    ),
    conflict_requires_review=False,
    rationale=(
        "One assessment per player per cutoff per method. A later cutoff is "
        "a new slot: that is how an evolving injury picture is represented. "
        "A different assessment at the same cutoff from the same method "
        "means the underlying observations changed, which is recorded as a "
        "conflict but does not by itself require a human - the newer "
        "observation is visible in the lineage and a later cutoff will "
        "supersede it."
    ),
)

PRICE_OBSERVATION = EntityIdentity(
    entity="price_observation",
    logical_fields=(
        "canonical_game_id",
        "market",
        "selection",
        "observed_at",
        "cohort",
        "provider_mode",
        "source_identity",
    ),
    content_fields=(
        "line",
        "american",
        "decimal_odds",
        "break_even_probability",
        "confirmed",
        "source",
        "policy_version",
        "code_commit",
    ),
    conflict_requires_review=True,
    rationale=(
        "Line and price are deliberately NOT in the logical identity. The "
        "same submission reporting a different number is the case that must "
        "be caught: a person re-entering the same observation with a "
        "different price has either mistyped or is looking at a changed "
        "market, and both need to be visible rather than silently stored as "
        "two equally valid observations of one moment. A new observation "
        "time or a new submission identity IS a new slot, which is how a "
        "genuinely later price is recorded. A correction supersedes through "
        "a new governed identity; it never edits the row it corrects."
    ),
)

ALL_IDENTITIES: tuple[EntityIdentity, ...] = (
    CONSENSUS, EVALUATION, AVAILABILITY, PRICE_OBSERVATION,
)

BY_ENTITY: dict[str, EntityIdentity] = {i.entity: i for i in ALL_IDENTITIES}


def specification() -> dict[str, Any]:
    """The full identity specification, for documentation and tests."""
    return {
        "logical_identity_version": LOGICAL_IDENTITY_VERSION,
        "content_hash_version": CONTENT_HASH_VERSION,
        "precision_decimal_places": _PRECISION,
        "digest": "sha256",
        "entities": [i.as_spec() for i in ALL_IDENTITIES],
    }


def require_known_versions(logical_version: str, content_version: str) -> None:
    """Refuse to compare hashes produced by a version we cannot reproduce.

    Silently treating an unknown version as comparable would let a record
    hashed under different rules be read as identical or conflicting when
    neither conclusion is supportable.
    """
    if logical_version != LOGICAL_IDENTITY_VERSION:
        raise UnknownHashVersion(
            f"logical identity version {logical_version!r} is not "
            f"{LOGICAL_IDENTITY_VERSION!r}; this code cannot reproduce it"
        )
    if content_version != CONTENT_HASH_VERSION:
        raise UnknownHashVersion(
            f"content hash version {content_version!r} is not "
            f"{CONTENT_HASH_VERSION!r}; this code cannot reproduce it"
        )


# --------------------------------------------------------------------------- #
# The concurrency-safe insert
# --------------------------------------------------------------------------- #


def upsert_by_identity(
    session: Any,
    model: Any,
    *,
    identity: EntityIdentity,
    logical_values: dict[str, Any],
    content_values: dict[str, Any],
    build: Any,
) -> IdentityResult:
    """Insert, or return the row that already holds this logical identity.

    The DATABASE decides, not a pre-check. `SELECT -> not found -> INSERT`
    is race-prone by construction: two callers both see nothing and both
    insert, and on SQLite they usually get away with it while PostgreSQL
    enforces the constraint. So the insert is attempted inside a SAVEPOINT
    and the unique violation is the signal.

    Losing the race is not an error. It means somebody else already
    established the slot, and the only remaining question is whether they
    put the same thing in it.

    The savepoint matters: a failed INSERT poisons the transaction, and
    without one the caller's outer transaction would be unusable after a
    perfectly ordinary retry.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    lid = identity.logical_hash(logical_values)
    cid = identity.content_hash(content_values)

    def _fetch() -> Any:
        return session.scalars(
            select(model).where(
                model.logical_identity_hash == lid,
                model.logical_identity_version == LOGICAL_IDENTITY_VERSION,
            )
        ).first()

    # A cheap read first. Not for correctness - the savepoint below is what
    # provides that - but because the overwhelmingly common case is a
    # retry, and a failed insert per retry is wasteful.
    existing = _fetch()
    if existing is None:
        row = build()
        row.logical_identity_version = LOGICAL_IDENTITY_VERSION
        row.logical_identity_hash = lid
        row.content_hash_version = CONTENT_HASH_VERSION
        row.content_hash = cid
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
            return IdentityResult(IdentityOutcome.CREATED, row, lid, cid)
        except IntegrityError as exc:
            # Only a violation of THIS constraint means somebody else won
            # the race. Anything else is a real defect and must not be
            # laundered into an idempotency result.
            if not _is_identity_violation(exc, model):
                raise
            # The savepoint rollback usually detaches the pending instance
            # already, so an unconditional expunge raises InvalidRequestError
            # and turns a perfectly ordinary lost race into a crash. Losing
            # a race is not an error; it must not look like one.
            if row in session:
                session.expunge(row)
            existing = _fetch()
            if existing is None:  # pragma: no cover - would mean the
                # constraint fired for a row we cannot then find, which is
                # not a state this code can reason about.
                raise
    return _compare(identity, existing, lid, cid, content_values)


def _is_identity_violation(exc: Any, model: Any) -> bool:
    """True only for a unique violation on the logical-identity index.

    Swallowing every IntegrityError would turn a genuine constraint bug -
    a null in a NOT NULL column, a broken foreign key - into a silent
    "already exists", which is the worst possible way to lose a defect.
    """
    text = str(getattr(exc, "orig", exc)).lower()
    table = getattr(model, "__tablename__", "")
    markers = ("logical_identity", f"uq_{table}_logical_identity")
    return any(m in text for m in markers)


def _compare(
    identity: EntityIdentity,
    existing: Any,
    lid: str,
    cid: str,
    content_values: dict[str, Any],
) -> IdentityResult:
    """Identical retry, or contradiction?"""
    require_known_versions(
        existing.logical_identity_version, existing.content_hash_version
    )
    if existing.content_hash == cid:
        return IdentityResult(IdentityOutcome.EXISTING_IDENTICAL, existing, lid, cid)

    # A conflict names WHICH fields disagree where it can. A bare "the
    # hashes differ" leaves a human to diff two rows by hand, which is
    # exactly the position the parity comparator used to leave people in.
    fields = tuple(
        name for name in identity.content_fields
        if name in content_values
    )
    return IdentityResult(
        IdentityOutcome.CONFLICT,
        existing,
        lid,
        cid,
        existing_content_hash=existing.content_hash,
        conflict_fields=fields,
        requires_manual_review=identity.conflict_requires_review,
    )
