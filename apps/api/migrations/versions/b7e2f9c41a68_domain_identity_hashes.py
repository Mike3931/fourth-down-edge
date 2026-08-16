"""Persist logical identity and content hashes; enforce uniqueness on the identity.

Four services had a service-level idempotency pre-check: SELECT, find
nothing, INSERT. That is race-prone by construction - two callers both see
nothing and both insert - and it passed for months because SQLite
serialises writers at the file level while PostgreSQL does not. The pre-check
stays for cheapness; the DATABASE is now what decides.

Two hashes, not one, because two different questions were being conflated:

  logical_identity_hash   WHICH record is this - the slot being aimed at
  content_hash            WHAT it says

Same slot + same content is a retry and must be a no-op. Same slot +
different content is a contradiction. Both look like "the row exists"; only
the pair of hashes tells them apart.

Order of operations matters and is deliberate:

  1. add nullable columns
  2. backfill deterministically from existing content
  3. INVENTORY duplicates before constraining anything
  4. fail loudly on conflicting duplicates
  5. only then create the unique index

Creating the index first would have failed with a bare constraint violation
naming nothing, leaving an operator to work out which rows collided and
why. The inventory is the difference between "migration failed" and
"migration failed, here are the 3 affected identities and whether their
content agrees".

Revision ID: b7e2f9c41a68
Revises: a4d81c6b0e57
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2f9c41a68"
down_revision: str | None = "a4d81c6b0e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, entity name in the identity registry)
_TABLES: tuple[tuple[str, str], ...] = (
    ("consensus_snapshots", "consensus_snapshot"),
    ("forward_ledger", "research_evaluation"),
    ("availability_assessments", "availability_assessment"),
    ("manual_book_price_entries", "price_observation"),
)

_AUDIT = (
    Path(__file__).resolve().parents[4]
    / "reports" / "integrity" / "identity-migration-audit.json"
)


class _LazyResolution:
    """The governed resolution mechanism, imported at call time.

    Alembic loads every revision module in the chain on any command; a
    top-level application import would make unrelated migrations depend on
    the application package being importable.
    """

    def __getattr__(self, name: str):
        from fde_api.forward import identity_resolution

        return getattr(identity_resolution, name)


_resolution = _LazyResolution()


class ConflictingDuplicatesFound(RuntimeError):
    """Rows share a logical identity but disagree about content.

    Not something a migration may resolve. Picking one would silently
    discard a record somebody wrote, and the two disagree about a fact the
    system treats as true.
    """


# The versions and field sets THIS revision hashed with, frozen here.
#
# These used to be read live from `fde_api.forward.domain_identity`. That
# was invisible while the live constants still said v1 and became wrong the
# moment `d8f41c6a3b92` moved them to v2: the version string is mixed INTO
# the digest, and the allowlist decides which fields reach it, so borrowing
# either made a later commit silently change what this migration writes -
# and it stamped the borrowed version on the result, claiming current code
# could reproduce a hash it computed under different rules.
#
# A migration describes the schema AND the semantics at a point in history.
# It may not import a constant a later commit can redefine. Same argument
# `d8f41c6a3b92` makes for spelling out the cohort vocabulary rather than
# importing the enum.
#
# `cohort` here renders the row's DATA MODE, because that is what this
# revision meant by the word. Under v2 it means the row's cohort column,
# which does not exist at this revision. Hence v1, honestly labelled: rows
# backfilled here are refused by `require_known_versions`, which is correct
# - this code cannot reproduce them.
_V1_LOGICAL = "domain-logical-identity-v1"
_V1_CONTENT = "domain-content-v1"

_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "consensus_snapshot": (
        ("canonical_game_id", "market", "cutoff", "cohort", "method_version"),
        ("median_line", "home_price_american", "away_price_american",
         "over_price_american", "under_price_american", "no_vig_home_prob",
         "no_vig_over_prob", "eligible_books", "quote_lineage",
         "line_dispersion", "price_dispersion", "provider_mode"),
    ),
    "research_evaluation": (
        ("canonical_game_id", "prediction_identity", "price_identity",
         "evaluation_type", "cohort", "policy_version", "model_version",
         "decision_context_hash"),
        ("status", "model_probability", "conservative_probability",
         "break_even_probability", "expected_value", "decision_reason_codes",
         "suppressed", "data_completeness", "execution_eligible"),
    ),
    "availability_assessment": (
        ("canonical_game_id", "player_id", "cutoff", "cohort", "method_version"),
        ("state", "active_prob_low", "active_prob_high", "snap_share_low",
         "snap_share_high", "confidence_tier", "is_starting_qb",
         "observation_lineage", "missing_data"),
    ),
    "price_observation": (
        ("canonical_game_id", "market", "selection", "observed_at", "cohort",
         "provider_mode", "source_identity"),
        ("line", "american", "decimal_odds", "break_even_probability",
         "confirmed", "source", "policy_version", "code_commit"),
    ),
}


class _FrozenIdentity:
    """This revision's hashing, independent of the live module.

    Only `canonical_payload` and `digest` are borrowed: they are the
    RENDERING (float precision, timestamp normalisation, missing-vs-null),
    and a change to either is required to bump the version string, which
    is pinned above - so a drift there cannot pass silently.
    """

    def __init__(self, logical: tuple[str, ...], content: tuple[str, ...]) -> None:
        self.logical_fields = logical
        self.content_fields = content

    def logical_hash(self, values: dict) -> str:
        di = _identity_module()
        return di.digest(
            di.canonical_payload(values, self.logical_fields), version=_V1_LOGICAL
        )

    def content_hash(self, values: dict) -> str:
        di = _identity_module()
        return di.digest(
            di.canonical_payload(values, self.content_fields), version=_V1_CONTENT
        )


def _identity_for(entity: str) -> _FrozenIdentity:
    return _FrozenIdentity(*_FIELDS[entity])


def _row_values(table: str, row: sa.Row) -> tuple[dict, dict]:
    """The logical and content field values for one existing row.

    Read positionally from the row mapping so the migration does not depend
    on the ORM, which will have moved on by the time anyone runs this
    against an old database.
    """
    m = row._mapping
    if table == "consensus_snapshots":
        logical = {
            "canonical_game_id": m["canonical_game_id"],
            "market": m["market"],
            "cutoff": m["observed_at"],
            "cohort": m["data_mode"],
            "method_version": m["method_version"],
        }
        content = {
            "median_line": m["median_line"],
            "home_price_american": m["home_price_american"],
            "away_price_american": m["away_price_american"],
            "over_price_american": m["over_price_american"],
            "under_price_american": m["under_price_american"],
            "no_vig_home_prob": m["no_vig_home_prob"],
            "no_vig_over_prob": m["no_vig_over_prob"],
            "eligible_books": m["eligible_books"],
            "quote_lineage": m["quote_ids"],
            "line_dispersion": m["line_dispersion"],
            "price_dispersion": m["price_dispersion"],
            "provider_mode": m["provider_mode"],
        }
    elif table == "forward_ledger":
        logical = {
            "canonical_game_id": m["canonical_game_id"],
            "prediction_identity": m["forward_prediction_id"],
            "price_identity": f"{m['market']}|{m['selection']}|"
                              f"{m['qualifying_line']}|{m['qualifying_american']}",
            "evaluation_type": m["horizon"],
            "cohort": m["data_mode"],
            "policy_version": m["policy_version"],
            "model_version": m["model_version"],
            # Pre-migration rows have no stored decision context. The cutoff
            # stands in: it is the one field that distinguishes successive
            # evaluations of the same selection, so backfilled identities
            # stay distinct without inventing a context that was never
            # computed.
            "decision_context_hash": f"legacy:{m['as_of_at']}",
        }
        content = {
            "status": m["status"],
            "model_probability": m["model_probability"],
            "conservative_probability": m["conservative_probability"],
            "break_even_probability": m["break_even_probability"],
            "expected_value": m["expected_value"],
            "decision_reason_codes": (m["reasons"] or {}).get("codes")
            if isinstance(m["reasons"], dict) else None,
            "suppressed": None,
            "data_completeness": m["data_completeness"],
            "execution_eligible": m["filled"],
        }
    elif table == "availability_assessments":
        logical = {
            "canonical_game_id": m["canonical_game_id"],
            "player_id": m["player_id"],
            "cutoff": m["as_of_at"],
            "cohort": m["data_mode"],
            "method_version": "availability-v0",
        }
        content = {
            "state": m["state"],
            "active_prob_low": m["active_prob_low"],
            "active_prob_high": m["active_prob_high"],
            "snap_share_low": m["snap_share_low"],
            "snap_share_high": m["snap_share_high"],
            "confidence_tier": m["confidence_tier"],
            "is_starting_qb": m["is_starting_qb"],
            "observation_lineage": None,
            "missing_data": m["missing_data"],
        }
    else:  # manual_book_price_entries
        logical = {
            "canonical_game_id": m["canonical_game_id"],
            "market": m["market"],
            "selection": m["selection"],
            "observed_at": m["observed_at"],
            "cohort": m["cohort"],
            "provider_mode": m["provider_mode"],
            "source_identity": m["user_id"],
        }
        content = {
            "line": m["line"],
            "american": m["american"],
            "decimal_odds": m["decimal_odds"],
            "break_even_probability": m["break_even_probability"],
            "confirmed": m["confirmed_at"] is not None,
            "source": m["source"],
            "policy_version": m["policy_version"],
            "code_commit": m["code_commit"],
        }
    return logical, content


def _ensure_aware(values: dict) -> dict:
    """SQLite hands back naive datetimes; the canonicaliser refuses them.

    Assumed UTC because every timestamp this system writes is UTC - the
    naivety is a storage artefact of SQLite, not an unknown offset.
    """
    return {
        k: (v.replace(tzinfo=UTC) if isinstance(v, datetime) and v.tzinfo is None else v)
        for k, v in values.items()
    }


def upgrade() -> None:
    bind = op.get_bind()

    # An explicitly authored manifest, or nothing. The default is nothing: a
    # populated database with duplicate identities does not upgrade until a
    # human has written down what those rows mean.
    manifest = _resolution.ResolutionManifest.load(
        os.environ.get("FDE_IDENTITY_RESOLUTION_MANIFEST")
    )
    applied: list[dict] = []
    refusals: list[str] = []

    for table, _entity in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("logical_identity_version", sa.String(length=48)))
            batch.add_column(sa.Column("logical_identity_hash", sa.String(length=64)))
            batch.add_column(sa.Column("content_hash_version", sa.String(length=48)))
            batch.add_column(sa.Column("content_hash", sa.String(length=64)))

    audit: dict = {
        "revision": revision,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "tables": {},
    }

    for table, entity in _TABLES:
        identity = _identity_for(entity)
        rows = list(bind.execute(sa.text(f"SELECT * FROM {table}")))
        groups: dict[str, list[tuple]] = {}

        for row in rows:
            logical, content = _row_values(table, row)
            lid = identity.logical_hash(_ensure_aware(logical))
            cid = identity.content_hash(_ensure_aware(content))
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET logical_identity_version=:lv, "
                    "logical_identity_hash=:lh, content_hash_version=:cv, "
                    "content_hash=:ch WHERE id=:rid"
                ),
                {
                    # This revision's versions, not today's - see _FIELDS.
                    "lv": _V1_LOGICAL, "lh": lid,
                    "cv": _V1_CONTENT, "ch": cid,
                    "rid": row._mapping["id"],
                },
            )
            groups.setdefault(lid, []).append((row._mapping["id"], cid))

        unique = [k for k, v in groups.items() if len(v) == 1]
        exact = [k for k, v in groups.items()
                 if len(v) > 1 and len({c for _, c in v}) == 1]
        conflicting = [k for k, v in groups.items()
                       if len(v) > 1 and len({c for _, c in v}) > 1]

        audit["tables"][table] = {
            "entity": entity,
            "rows": len(rows),
            "distinct_logical_identities": len(groups),
            "UNIQUE": len(unique),
            "EXACT_DUPLICATE": len(exact),
            "CONFLICTING_DUPLICATE": len(conflicting),
            "exact_duplicate_identities": exact[:50],
            "conflicting_duplicate_identities": conflicting[:50],
            "conflicting_duplicate_row_ids": [
                [rid for rid, _ in groups[k]] for k in conflicting[:20]
            ],
        }

        # Every duplicate group must be authorised INDIVIDUALLY. There is no
        # blanket approval and no "it is only development data" exemption:
        # the manifest names the rows, restates the content hashes it was
        # written against, and says who authorised it. A group the manifest
        # does not cover — or covers as MANUAL_REVIEW_UNRESOLVED — keeps the
        # migration refused, and nothing about that group is touched.
        for lid in exact + conflicting:
            plan = _resolution.plan_group(
                table=table,
                logical_identity_hash=lid,
                rows=list(groups[lid]),
                manifest=manifest,
            )
            if plan.resolved:
                applied.append(_resolution.apply_plan(bind, plan))
            else:
                refusals.extend(plan.problems)

    audit["resolution_manifest"] = manifest.source
    audit["resolutions_applied"] = applied
    audit["refusals"] = refusals
    _AUDIT.parent.mkdir(parents=True, exist_ok=True)
    _AUDIT.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")

    if refusals:
        # The audit is written FIRST, deliberately: a refusal an operator
        # cannot inspect is only half a refusal. They need to see which
        # identities collided and what the rows disagree about before they
        # can write a manifest that resolves them.
        raise ConflictingDuplicatesFound(
            f"{len(refusals)} duplicate identity group(s) are not resolved by the "
            f"manifest ({manifest.source}). The unique constraint was NOT added "
            f"and nothing was changed for these groups. See {_AUDIT}.\n  - "
            + "\n  - ".join(refusals[:20])
        )

    # Only now, with every group either unique or explicitly resolved, is
    # the constraint safe to add.
    for table, _entity in _TABLES:
        op.create_index(
            f"uq_{table}_logical_identity",
            table,
            ["logical_identity_version", "logical_identity_hash"],
            unique=True,
        )


def _identity_module():
    from fde_api.forward import domain_identity

    return domain_identity


def downgrade() -> None:
    """Structurally supported, semantically lossy.

    Dropping the columns discards every stored identity and content hash.
    A re-upgrade recomputes them from current content - which is correct for
    rows unchanged since, and silently WRONG for any row whose content was
    edited in between, because the recomputed content hash would then
    describe the edit rather than the original observation. The tables are
    append-only, so that should not arise; it is recorded because "should
    not arise" is not the same as "cannot".
    """
    for table, _entity in _TABLES:
        op.drop_index(f"uq_{table}_logical_identity", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("content_hash")
            batch.drop_column("content_hash_version")
            batch.drop_column("logical_identity_hash")
            batch.drop_column("logical_identity_version")
