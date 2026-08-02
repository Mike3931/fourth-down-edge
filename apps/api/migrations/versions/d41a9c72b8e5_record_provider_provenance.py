"""Record provider provenance on captured and derived market records.

Without a provider_mode column, a fixture payload and a live provider
response produce byte-identical rows. Nothing downstream could then honour
the rule that fixture output is never stored or described as live output.

Existing rows are backfilled to UNKNOWN_LEGACY rather than LIVE. Their
true provenance is genuinely unknown, and guessing LIVE would manufacture
exactly the false claim this column exists to prevent. This mirrors the
UNKNOWN_LEGACY treatment already used for scheduler domain state.

Revision ID: d41a9c72b8e5
Revises: abc511bec1a3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d41a9c72b8e5"
down_revision: str | None = "abc511bec1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("odds_quotes", "consensus_snapshots")

# MIXED and UNKNOWN_LEGACY are conclusions rather than origins, but both are
# legitimately storable: MIXED is derived, UNKNOWN_LEGACY is backfilled.
_VOCABULARY = (
    "FIXTURE",
    "SANDBOX",
    "LIVE",
    "UNAVAILABLE",
    "KEY_MISSING",
    "QUOTA_EXHAUSTED",
    "MIXED",
    "UNKNOWN_LEGACY",
)


def upgrade() -> None:
    quoted = ", ".join(f"'{v}'" for v in _VOCABULARY)
    for table in _TABLES:
        # Batch mode, not a bare ALTER: SQLite cannot add a CHECK constraint
        # in place and needs the copy-and-move strategy. PostgreSQL runs the
        # same operations as plain ALTERs, so one path serves both.
        with op.batch_alter_table(table) as batch:
            # server_default is required, not optional: the column is NOT
            # NULL and these tables may already hold rows.
            batch.add_column(
                sa.Column(
                    "provider_mode",
                    sa.String(length=16),
                    nullable=False,
                    server_default="UNKNOWN_LEGACY",
                )
            )
            batch.create_check_constraint(
                f"ck_{table}_provider_mode_vocabulary",
                f"provider_mode IN ({quoted})",
            )
        op.create_index(f"ix_{table}_provider_mode", table, ["provider_mode"])


def downgrade() -> None:
    """Structurally supported, semantically lossy.

    Dropping the column discards the provenance of every record captured
    after the upgrade. A re-upgrade backfills them to UNKNOWN_LEGACY, so
    quotes that were known to be FIXTURE or LIVE become indistinguishable.
    The downgrade path is preserved for schema rollback, not for data
    round-tripping.
    """
    for table in _TABLES:
        op.drop_index(f"ix_{table}_provider_mode", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(
                f"ck_{table}_provider_mode_vocabulary", type_="check"
            )
            batch.drop_column("provider_mode")
