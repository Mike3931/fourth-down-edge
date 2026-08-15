"""Give three tables a real cohort, because their identity already claimed one.

CONSENSUS, EVALUATION and AVAILABILITY each declare a logical identity field
named `cohort`. Three of the four call sites passed `data_mode.value` into
it:

    consensus.py   "cohort": data_mode.value
    ledger.py      "cohort": data_mode.value
    injuries.py    "cohort": data_mode.value

PRICE_OBSERVATION is the fourth, and it passes `obs.cohort.value` — the
right thing, and proof the pattern was understood.

`DataMode` has two values. `Cohort` has four, and BURN_IN and
OFFICIAL_FORWARD_TEST BOTH write LIVE_RESEARCH. So a burn-in consensus and
an official consensus for the same game, market and cutoff produce the
identical logical identity hash — verified — with different content. That
is a CONFLICT on every game and every market, and the same holds for the
forward ledger, which is the evaluation record itself.

It has stayed invisible because only one cohort has ever run. It becomes
unavoidable the moment a second one does, which is the point of running a
burn-in cohort beside the official forward test.

BACKFILL

Existing rows get `unknown_legacy`, mirroring `provider_mode`'s
UNKNOWN_LEGACY: these rows predate cohort discipline on these tables and
their true cohort is not recorded anywhere. Guessing `burn_in` would
manufacture a provenance they do not have, and it is the value that would
collide with the burn-in cohort about to start.

`unknown_legacy` is deliberately NOT in the `Cohort` enum. The enum governs
what new code may write; this column also holds history, and history
contains a state new code must never produce.

NO SERVER DEFAULT, and NOT NULL after the backfill. A default would let an
insert that forgot the cohort be silently filed as legacy, which is the
class of defect this whole change exists to remove.

Revision ID: d8f41c6a3b92
Revises: b7e2f9c41a68
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f41c6a3b92"
down_revision: str | None = "b7e2f9c41a68"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLES = ("consensus_snapshots", "forward_ledger", "availability_assessments")
LEGACY = "unknown_legacy"
VOCABULARY = ("fixture", "demo", "burn_in", "official_forward_test", LEGACY)

# Spelled out rather than imported from `fde_api.forward.cohort`. A
# migration describes the schema at a point in history; importing today's
# constant would let a later edit to the enum silently rewrite what this
# migration did.
_IN = ", ".join(f"'{v}'" for v in VOCABULARY)


def upgrade() -> None:
    for table in TABLES:
        # 1. nullable, so the backfill has somewhere to land.
        op.add_column(table, sa.Column("cohort", sa.String(24), nullable=True))
        # 2. backfill every existing row as legacy — see the note above on
        #    why not `burn_in`.
        op.execute(
            sa.text(f"UPDATE {table} SET cohort = :legacy WHERE cohort IS NULL")
            .bindparams(legacy=LEGACY)
        )
        # 3. only now NOT NULL, so a future insert that omits the cohort
        #    fails loudly instead of inheriting a default.
        with op.batch_alter_table(table) as batch:
            batch.alter_column("cohort", existing_type=sa.String(24), nullable=False)
            batch.create_check_constraint(
                f"ck_{table}_cohort_vocabulary", f"cohort IN ({_IN})"
            )
        op.create_index(f"ix_{table}_cohort", table, ["cohort"])

    # The book minimum that admitted a consensus. `eligible_books` records
    # how many turned up; nothing recorded how many were required, so a
    # one-book burn-in snapshot and a one-book snapshot under a
    # three-book rule (which cannot exist, but a reader cannot tell that
    # from the row) looked identical.
    #
    # Backfilled to 3 because 3 is what `build_consensus` required for
    # every row now in this table — not a default, a fact about the code
    # that wrote them.
    op.add_column(
        "consensus_snapshots", sa.Column("min_books_applied", sa.Integer, nullable=True)
    )
    op.execute("UPDATE consensus_snapshots SET min_books_applied = 3 "
               "WHERE min_books_applied IS NULL")
    with op.batch_alter_table("consensus_snapshots") as batch:
        batch.alter_column(
            "min_books_applied", existing_type=sa.Integer, nullable=False
        )


def downgrade() -> None:
    op.drop_column("consensus_snapshots", "min_books_applied")
    for table in TABLES:
        op.drop_index(f"ix_{table}_cohort", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_cohort_vocabulary", type_="check")
        op.drop_column(table, "cohort")
