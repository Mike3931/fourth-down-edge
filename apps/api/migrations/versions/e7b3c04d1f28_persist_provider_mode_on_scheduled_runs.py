"""Persist provider mode on scheduled job runs.

A recovery may not change the provider mode: a run that captured fixture
output must not be recovered as a live capture, or the recovery would
launder fixture data into the live cohort. `validate_recovery` was written
to enforce that and could not, because `scheduled_job_runs` did not store
the mode. It carried this comment instead:

    Provider-mode invariance is NOT enforced here: scheduled_job_runs does
    not persist provider mode, so there is nothing to compare against. A
    check that cannot fail would be worse than none.

That was the honest state of it, and this migration is what makes the
check possible. The column is added; enforcement follows in the service
layer, guarded by tests that fail if the comparison is removed.

Existing rows are backfilled to UNKNOWN_LEGACY, matching the treatment
already used for domain state and for provider provenance on the market
tables. Their true mode is genuinely unknown, and defaulting to LIVE would
manufacture the exact claim the column exists to prevent. A recovery of an
UNKNOWN_LEGACY run therefore cannot be checked for mode invariance, and
the service treats that as a reason to require review rather than as
permission to proceed.

Revision ID: e7b3c04d1f28
Revises: d41a9c72b8e5
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3c04d1f28"
down_revision: str | None = "d41a9c72b8e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "scheduled_job_runs"

# Same vocabulary as the market tables, deliberately: a run's mode and the
# mode stamped on the records it produced must be comparable values, not
# two dialects that happen to look alike.
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
    # Batch mode, not a bare ALTER: SQLite cannot add a CHECK constraint in
    # place and needs copy-and-move. PostgreSQL runs the same operations as
    # plain ALTERs, so one path serves both backends.
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(
            sa.Column(
                "provider_mode",
                sa.String(length=16),
                nullable=False,
                server_default="UNKNOWN_LEGACY",
            )
        )
        batch.create_check_constraint(
            f"ck_{_TABLE}_provider_mode_vocabulary",
            f"provider_mode IN ({quoted})",
        )
    op.create_index(f"ix_{_TABLE}_provider_mode", _TABLE, ["provider_mode"])


def downgrade() -> None:
    """Structurally supported, semantically lossy.

    Dropping the column discards the provider mode of every run recorded
    after the upgrade, and a re-upgrade backfills them all to
    UNKNOWN_LEGACY. A run known to have been FIXTURE becomes
    indistinguishable from one known to have been LIVE, which is precisely
    the confusion the column prevents. Preserved for schema rollback, not
    for data round-tripping.
    """
    op.drop_index(f"ix_{_TABLE}_provider_mode", table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(f"ck_{_TABLE}_provider_mode_vocabulary", type_="check")
        batch.drop_column("provider_mode")
