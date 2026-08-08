"""Governed resolution of logical-identity duplicates.

The migration refuses to constrain a table that already contains duplicate
logical identities. That refusal is correct and must stay: a migration that
picks a winner is discarding a record somebody wrote, and it does so at a
moment when nobody is watching.

This is the other half — the explicit, authored, reviewable path by which a
human decides what those rows mean. It is deliberately inconvenient. The
manifest must name the rows, restate the content hashes it was written
against, give a rationale, and name who authorised it. If any of that is
missing or stale, resolution refuses and the migration stays refused.

Four dispositions, and the distinction between them is the whole point:

  KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION
      The rows are legitimately different records and the IDENTITY was
      wrong to merge them. The fix is to the identity, not to the data:
      each row carries a correction that distinguishes it, and every row
      stays current. Illegal when the rows are byte-identical — there is
      nothing to distinguish.

  CANONICALIZE_EXACT_DUPLICATES
      One record written twice. Legal only when every content hash in the
      group agrees. One row is retained; downstream references are
      repointed to it; the others are archived.

  SELECT_AUTHORITATIVE_AND_SUPERSEDE
      The rows disagree, and evidence outside the rows settles which is
      right. The authoritative row is named and the evidence recorded.
      Downstream references are NOT repointed: they record what was
      actually used at the time, and rewriting them would falsify history.

  MANUAL_REVIEW_UNRESOLVED
      Honest default. Not a resolution — it records that a human looked and
      could not settle it. The migration stays refused.

No disposition deletes a row. Archival and supersession move a row out of
the CURRENT identity namespace (`logical_identity_version`) instead, which
satisfies the unique constraint while leaving the evidence intact and
findable. A destructive path is not offered, because the moment one exists
it becomes the convenient one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "identity-resolution-manifest-v1"

# Namespaces a resolved row can be moved into. They are values of
# `logical_identity_version`, so a moved row can never again collide with a
# current one, and every query that filters on the current version stops
# seeing it — without anything being deleted.
CURRENT_NAMESPACE = "domain-logical-identity-v1"
ARCHIVED_NAMESPACE = "domain-logical-identity-v1/archived-exact-duplicate"
SUPERSEDED_NAMESPACE = "domain-logical-identity-v1/superseded"


class Disposition(str, Enum):
    KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION = "KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION"
    CANONICALIZE_EXACT_DUPLICATES = "CANONICALIZE_EXACT_DUPLICATES"
    SELECT_AUTHORITATIVE_AND_SUPERSEDE = "SELECT_AUTHORITATIVE_AND_SUPERSEDE"
    MANUAL_REVIEW_UNRESOLVED = "MANUAL_REVIEW_UNRESOLVED"


RESOLVING = {
    Disposition.KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION,
    Disposition.CANONICALIZE_EXACT_DUPLICATES,
    Disposition.SELECT_AUTHORITATIVE_AND_SUPERSEDE,
}


class ResolutionRefused(RuntimeError):
    """The manifest does not authorise what it is being asked to do."""


# The only tables a manifest may resolve.
#
# `table` is read from an operator-authored file and interpolated into the
# UPDATE statements below, because a table name cannot be a bound
# parameter. Nothing untrusted reaches it today - the path comes from an
# environment variable and anyone who can write that file already controls
# the deployment - so this is not closing an exploit. It is refusing to
# rely on that argument: the set of tables carrying a logical identity is
# fixed and known, so accepting anything else is a typo away from an
# UPDATE against a table nobody meant to touch.
#
# Mirrors `_TABLES` in the identity migration.
RESOLVABLE_TABLES: frozenset[str] = frozenset({
    "consensus_snapshots",
    "forward_ledger",
    "availability_assessments",
    "manual_book_price_entries",
})


class UnknownTable(ResolutionRefused):
    """A manifest named a table that carries no logical identity."""


def _assert_resolvable(table: str) -> None:
    """Last line before a table name becomes SQL text."""
    if table not in RESOLVABLE_TABLES:
        raise UnknownTable(
            f"{table!r} is not a resolvable table; expected one of "
            f"{sorted(RESOLVABLE_TABLES)}"
        )


@dataclass(frozen=True)
class ResolutionEntry:
    table: str
    logical_identity_hash: str
    row_ids: list[int]
    disposition: Disposition
    rationale: str
    authorised_by: str
    # The content hashes the author SAW. If the database has moved since,
    # the manifest describes a database that no longer exists and must not
    # be applied — the author consented to something else.
    content_hashes_at_authoring: list[str]
    retain_row_id: int | None = None
    # row id -> the discriminator that makes it a distinct record.
    identity_corrections: dict[int, str] = field(default_factory=dict)
    evidence: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ResolutionEntry:
        try:
            disposition = Disposition(raw["disposition"])
        except (KeyError, ValueError) as exc:
            raise ResolutionRefused(f"unknown disposition: {raw.get('disposition')}") from exc
        return cls(
            table=raw["table"],
            logical_identity_hash=raw["logical_identity_hash"],
            row_ids=[int(r) for r in raw["row_ids"]],
            disposition=disposition,
            rationale=raw.get("rationale", ""),
            authorised_by=raw.get("authorised_by", ""),
            content_hashes_at_authoring=sorted(raw.get("content_hashes_at_authoring", [])),
            retain_row_id=(
                int(raw["retain_row_id"]) if raw.get("retain_row_id") is not None else None
            ),
            identity_corrections={
                int(k): str(v) for k, v in (raw.get("identity_corrections") or {}).items()
            },
            evidence=raw.get("evidence", ""),
        )

    @property
    def key(self) -> tuple[str, str]:
        return (self.table, self.logical_identity_hash)

    def validate_shape(self) -> list[str]:
        """What is wrong with the entry, ignoring the database."""
        problems: list[str] = []
        if self.table not in RESOLVABLE_TABLES:
            problems.append(
                f"table {self.table!r} carries no logical identity; expected "
                f"one of {sorted(RESOLVABLE_TABLES)}"
            )
        if not self.rationale.strip():
            problems.append("no rationale")
        if not self.authorised_by.strip():
            problems.append("no authorising party")
        if len(self.row_ids) < 2:
            problems.append("a resolution names fewer than two rows")
        if not self.content_hashes_at_authoring:
            problems.append("no content hashes recorded at authoring time")

        if self.disposition is Disposition.CANONICALIZE_EXACT_DUPLICATES:
            if len(set(self.content_hashes_at_authoring)) != 1:
                problems.append(
                    "CANONICALIZE_EXACT_DUPLICATES on rows that are not identical; "
                    "canonicalising rows that disagree discards one of them"
                )
            if self.retain_row_id is None:
                problems.append("no retained row named")
        elif self.disposition is Disposition.SELECT_AUTHORITATIVE_AND_SUPERSEDE:
            if self.retain_row_id is None:
                problems.append("no authoritative row named")
            if not self.evidence.strip():
                problems.append(
                    "no evidence for the selection; choosing between disagreeing "
                    "rows requires a reason outside the rows themselves"
                )
        elif self.disposition is Disposition.KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION:
            if len(set(self.content_hashes_at_authoring)) == 1:
                problems.append(
                    "KEEP_DISTINCT on byte-identical rows; there is nothing to "
                    "distinguish, so they are duplicates and not distinct records"
                )
            missing = [r for r in self.row_ids if r not in self.identity_corrections]
            if missing:
                problems.append(f"no identity correction for rows {missing}")
            corrections = [self.identity_corrections.get(r) for r in self.row_ids]
            if len(set(corrections)) != len(corrections):
                problems.append(
                    "identity corrections are not distinct; they would still collide"
                )

        if self.retain_row_id is not None and self.retain_row_id not in self.row_ids:
            problems.append(f"retained row {self.retain_row_id} is not in the group")
        return problems


@dataclass(frozen=True)
class ResolutionManifest:
    entries: dict[tuple[str, str], ResolutionEntry]
    source: str = "(none)"

    @classmethod
    def empty(cls) -> ResolutionManifest:
        return cls(entries={})

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, source: str = "(inline)") -> ResolutionManifest:
        version = raw.get("artifact_schema_version")
        if version != MANIFEST_SCHEMA_VERSION:
            raise ResolutionRefused(
                f"manifest schema {version!r}, expected {MANIFEST_SCHEMA_VERSION!r}"
            )
        entries: dict[tuple[str, str], ResolutionEntry] = {}
        for raw_entry in raw.get("resolutions", []):
            entry = ResolutionEntry.from_dict(raw_entry)
            if entry.key in entries:
                raise ResolutionRefused(f"two resolutions for {entry.key}")
            entries[entry.key] = entry
        return cls(entries=entries, source=source)

    @classmethod
    def load(cls, path: Path | str | None) -> ResolutionManifest:
        if path is None:
            return cls.empty()
        p = Path(path)
        if not p.exists():
            return cls.empty()
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")), source=str(p))

    def for_group(self, table: str, logical_identity_hash: str) -> ResolutionEntry | None:
        return self.entries.get((table, logical_identity_hash))


@dataclass
class GroupPlan:
    """One duplicate group, and what the manifest authorises for it."""

    table: str
    logical_identity_hash: str
    row_ids: list[int]
    content_hashes: list[str]
    classification: str
    entry: ResolutionEntry | None
    problems: list[str]

    @property
    def resolved(self) -> bool:
        return (
            not self.problems
            and self.entry is not None
            and self.entry.disposition in RESOLVING
        )


def plan_group(
    *,
    table: str,
    logical_identity_hash: str,
    rows: list[tuple[int, str]],
    manifest: ResolutionManifest,
) -> GroupPlan:
    """Decide whether this duplicate group may proceed.

    `rows` is [(row_id, content_hash)] as it exists in the database RIGHT
    NOW, which is what the staleness check compares against.
    """
    row_ids = sorted(r for r, _ in rows)
    hashes = sorted(h for _, h in rows)
    classification = (
        "EXACT_DUPLICATE" if len(set(hashes)) == 1 else "CONFLICTING_DUPLICATE"
    )
    entry = manifest.for_group(table, logical_identity_hash)
    problems: list[str] = []

    if entry is None:
        problems.append(
            f"{table}/{logical_identity_hash[:12]}: {classification} with no "
            "resolution in the manifest"
        )
        return GroupPlan(table, logical_identity_hash, row_ids, hashes,
                         classification, None, problems)

    if entry.disposition is Disposition.MANUAL_REVIEW_UNRESOLVED:
        problems.append(
            f"{table}/{logical_identity_hash[:12]}: MANUAL_REVIEW_UNRESOLVED — "
            f"{entry.rationale or 'no rationale given'}"
        )
        return GroupPlan(table, logical_identity_hash, row_ids, hashes,
                         classification, entry, problems)

    problems.extend(
        f"{table}/{logical_identity_hash[:12]}: {p}" for p in entry.validate_shape()
    )

    # Staleness. Both halves matter: rows appearing and content changing are
    # different ways for the author's consent to have gone out of date.
    if sorted(entry.row_ids) != row_ids:
        problems.append(
            f"{table}/{logical_identity_hash[:12]}: manifest names rows "
            f"{sorted(entry.row_ids)}, database has {row_ids}"
        )
    if entry.content_hashes_at_authoring != hashes:
        problems.append(
            f"{table}/{logical_identity_hash[:12]}: content has changed since the "
            "manifest was authored; it authorises a database that no longer exists"
        )
    if (
        entry.disposition is Disposition.CANONICALIZE_EXACT_DUPLICATES
        and classification != "EXACT_DUPLICATE"
    ):
        problems.append(
            f"{table}/{logical_identity_hash[:12]}: CANONICALIZE on rows that "
            "disagree about content"
        )

    return GroupPlan(table, logical_identity_hash, row_ids, hashes,
                     classification, entry, problems)


# Downstream references, so canonicalisation can repoint them. Only
# relationships that actually exist are listed; an unlisted table is a
# table whose references are not repointed, which is why the applier
# reports what it repointed rather than assuming it got everything.
_REFERENCES: dict[str, tuple[tuple[str, str], ...]] = {
    "consensus_snapshots": (("closing_captures", "consensus_snapshot_id"),),
}


def apply_plan(bind: Any, plan: GroupPlan) -> dict[str, Any]:
    """Carry out one authorised resolution. Never deletes."""
    import sqlalchemy as sa

    entry = plan.entry
    if entry is None or not plan.resolved:
        raise ResolutionRefused(f"unresolved group: {plan.problems}")
    # Checked again here, not only at plan time. `apply_plan` is the
    # function that turns a name into SQL text, so it is the function that
    # has to be safe on its own - a caller that builds a GroupPlan by some
    # other route must not be able to route around the allowlist.
    _assert_resolvable(plan.table)

    action: dict[str, Any] = {
        "table": plan.table,
        "logical_identity_hash": plan.logical_identity_hash,
        "disposition": entry.disposition.value,
        "authorised_by": entry.authorised_by,
        "rationale": entry.rationale,
        "rows_deleted": 0,
        "repointed": {},
        "moved": {},
    }

    if entry.disposition is Disposition.KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION:
        # Every row stays current; the IDENTITY is what changes. The
        # corrected hash is a hash of the original plus the discriminator,
        # not a concatenation: the column holds 64 characters and a
        # concatenation would be silently truncated back into a collision.
        # The original hash and the discriminator are both recorded in the
        # action, so the derivation stays auditable.
        for row_id in plan.row_ids:
            correction = entry.identity_corrections[row_id]
            corrected = hashlib.sha256(
                f"{plan.logical_identity_hash}|{correction}".encode()
            ).hexdigest()
            bind.execute(
                sa.text(
                    f"UPDATE {plan.table} SET logical_identity_hash = :h "
                    "WHERE id = :rid"
                ),
                {"h": corrected, "rid": row_id},
            )
            action["moved"][row_id] = (
                f"identity corrected with {correction!r} -> {corrected[:12]}, "
                "still current"
            )
        return action

    retained = entry.retain_row_id
    others: list[Any] = [r for r in plan.row_ids if r != retained]

    if entry.disposition is Disposition.CANONICALIZE_EXACT_DUPLICATES:
        for ref_table, column in _REFERENCES.get(plan.table, ()):
            result = bind.execute(
                sa.text(
                    f"UPDATE {ref_table} SET {column} = :keep "
                    f"WHERE {column} IN :ids"
                ).bindparams(sa.bindparam("ids", expanding=True)),
                {"keep": retained, "ids": others or [None]},
            )
            action["repointed"][f"{ref_table}.{column}"] = result.rowcount
        namespace = ARCHIVED_NAMESPACE
        note = "archived exact duplicate"
    else:
        # SELECT_AUTHORITATIVE_AND_SUPERSEDE. References are left pointing
        # where they pointed: they record what was used, not what was right.
        namespace = SUPERSEDED_NAMESPACE
        note = "superseded, references intentionally left intact"

    for row_id in others:
        bind.execute(
            sa.text(
                f"UPDATE {plan.table} SET logical_identity_version = :ns "
                "WHERE id = :rid"
            ),
            {"ns": namespace, "rid": row_id},
        )
        action["moved"][row_id] = note
    action["retained"] = retained
    return action


def resolution_report(actions: list[dict[str, Any]], refusals: list[str]) -> dict[str, Any]:
    return {
        "artifact": "identity-duplicate-resolution",
        "artifact_schema_version": "identity-duplicate-resolution-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "applied": actions,
        "refused": refusals,
        "rows_deleted_total": sum(a["rows_deleted"] for a in actions),
        "note": (
            "No disposition deletes a row. Archived and superseded rows are "
            "moved out of the current identity namespace and remain readable."
        ),
    }
