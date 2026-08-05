"""The close becomes its own immutable record instead of a flag on a snapshot.

`closing_capture` used to set `consensus_snapshots.is_closing_capture = True`
on the chosen row. Two problems with that, one structural and one
expressive.

Structural: it mutated a record whose entire contract is immutability. A
consensus snapshot is an observation, and observations are appended, never
edited. Flipping a flag on one is a small edit, but it is the same kind of
edit the whole design forbids everywhere else.

Expressive: a flag can only say "this one". It cannot say which rule
selected it, which version of that rule, when the capture was scheduled
versus when it actually ran, or - most importantly - that NO eligible
snapshot existed and why. A missing close is the case where the record
matters most, and a boolean on a row that does not exist cannot record it.

So `closing_captures` is a new table that REFERENCES a snapshot and never
touches it. Identity is (game, market, selection, cohort, rule version):
a repeat capture of the same snapshot is idempotent, and a DIFFERENT
snapshot under the same identity is a conflict that is recorded alongside
the original rather than replacing it.

`is_closing_capture` is deliberately LEFT IN PLACE on consensus_snapshots.
Rows written before this migration carry it, and dropping the column would
destroy the only record of which snapshot was treated as the close during
that period. It is no longer written by any code path; a test pins that.

Revision ID: a4d81c6b0e57
Revises: f19c2a7d5e34
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d81c6b0e57"
down_revision: str | None = "f19c2a7d5e34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "closing_captures"

_COHORTS = ("fixture", "demo", "burn_in", "official_forward_test")
_STATUSES = ("CAPTURED", "MISSING", "CONFLICT")


def upgrade() -> None:
    cohorts = ", ".join(f"'{c}'" for c in _COHORTS)
    statuses = ", ".join(f"'{s}'" for s in _STATUSES)
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("canonical_game_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("selection", sa.String(length=16), nullable=True),
        sa.Column("consensus_snapshot_id", sa.Integer(), nullable=True, index=True),
        sa.Column("selection_rule", sa.Text(), nullable=False),
        sa.Column("selection_rule_version", sa.String(length=32), nullable=False, index=True),
        sa.Column("scheduled_slot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cohort", sa.String(length=24), nullable=False, index=True),
        sa.Column("data_mode", sa.String(length=16), nullable=False, index=True),
        sa.Column("provider_mode", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=True),
        sa.Column("code_commit", sa.String(length=48), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, index=True),
        sa.Column("missing_close_reason", sa.Text(), nullable=True),
        sa.Column("conflict_reason", sa.Text(), nullable=True),
        sa.Column("conflicts_with_id", sa.String(length=48), nullable=True),
        sa.Column("source_lineage", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({statuses})", name=f"ck_{_TABLE}_status_vocabulary"
        ),
        sa.CheckConstraint(
            f"cohort IN ({cohorts})", name=f"ck_{_TABLE}_cohort_vocabulary"
        ),
        # A captured close must reference a snapshot; a missing one must not.
        # In the database rather than only in the service, because a captured
        # close with no reference is unusable and a missing close carrying one
        # states something untrue.
        sa.CheckConstraint(
            "(status = 'CAPTURED' AND consensus_snapshot_id IS NOT NULL) OR "
            "(status = 'MISSING' AND consensus_snapshot_id IS NULL) OR "
            "(status = 'CONFLICT')",
            name=f"ck_{_TABLE}_reference_matches_status",
        ),
        sa.CheckConstraint(
            "status <> 'MISSING' OR missing_close_reason IS NOT NULL",
            name=f"ck_{_TABLE}_missing_has_reason",
        ),
        sa.CheckConstraint(
            "status <> 'CONFLICT' OR conflict_reason IS NOT NULL",
            name=f"ck_{_TABLE}_conflict_has_reason",
        ),
    )
    op.create_index(
        "ix_closing_capture_identity",
        _TABLE,
        ["canonical_game_id", "market", "selection", "cohort", "selection_rule_version"],
    )


def downgrade() -> None:
    """Structurally supported, semantically lossy.

    Dropping the table discards every recorded close: which snapshot the
    rule selected, which rule version selected it, every explicit
    missing-close reason, and every recorded conflict. The legacy
    `is_closing_capture` flag is not repopulated on the way down, because
    reconstructing it would mean guessing which of several captures was
    authoritative - and a guess is what this table exists to remove.
    """
    op.drop_index("ix_closing_capture_identity", table_name=_TABLE)
    op.drop_table(_TABLE)
