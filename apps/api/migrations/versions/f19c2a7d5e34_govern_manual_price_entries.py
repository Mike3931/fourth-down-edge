"""Give manually entered prices the governance fields every other record has.

`manual_book_price_entries` held the price and the two timestamps that
matter (when the user SAW it, when they typed it) but none of the fields
that make a record auditable: no cohort, no provider/source mode, no
policy version, no code commit, and no derived price representations.

That gap matters more here than elsewhere. A manually entered price is the
one record in the chain with no provider to vouch for it, so it needs MORE
provenance than a captured quote, not less. Without a cohort it could not
be kept out of official metrics; without a policy version a price entered
under one frozen policy was indistinguishable from one entered under
another.

Derived values (decimal odds, break-even probability) are stored rather
than recomputed on read so the record states what was true at entry. If
the conversion ever changes, historical rows keep the arithmetic they were
evaluated under instead of silently acquiring today's.

The software still never retrieves, scrapes, refreshes, or communicates
with bet365. This is about how a price the user typed is recorded.

Revision ID: f19c2a7d5e34
Revises: e7b3c04d1f28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f19c2a7d5e34"
down_revision: str | None = "e7b3c04d1f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "manual_book_price_entries"

_COHORTS = ("fixture", "demo", "burn_in", "official_forward_test")

_PROVIDER_MODES = (
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
    cohorts = ", ".join(f"'{c}'" for c in _COHORTS)
    modes = ", ".join(f"'{m}'" for m in _PROVIDER_MODES)
    with op.batch_alter_table(_TABLE) as batch:
        # Cohort defaults to `fixture`, not to a live cohort. A row whose
        # cohort was never recorded is not evidence about the forward test,
        # and the safe reading of an unlabelled row is the one that keeps it
        # out of official metrics.
        batch.add_column(
            sa.Column("cohort", sa.String(length=24), nullable=False,
                      server_default="fixture")
        )
        batch.add_column(
            sa.Column("provider_mode", sa.String(length=16), nullable=False,
                      server_default="UNKNOWN_LEGACY")
        )
        batch.add_column(sa.Column("policy_version", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("code_commit", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("decimal_odds", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("break_even_probability", sa.Float(), nullable=True)
        )
        batch.create_check_constraint(
            f"ck_{_TABLE}_cohort_vocabulary", f"cohort IN ({cohorts})"
        )
        batch.create_check_constraint(
            f"ck_{_TABLE}_provider_mode_vocabulary", f"provider_mode IN ({modes})"
        )
        # A probability outside (0, 1) is not a probability. Enforced in the
        # database because this row is the input to every downstream edge
        # calculation, and a bad value there propagates silently.
        batch.create_check_constraint(
            f"ck_{_TABLE}_break_even_range",
            "break_even_probability IS NULL OR "
            "(break_even_probability > 0 AND break_even_probability < 1)",
        )
        batch.create_check_constraint(
            f"ck_{_TABLE}_decimal_odds_range",
            "decimal_odds IS NULL OR decimal_odds > 1",
        )
    op.create_index(f"ix_{_TABLE}_cohort", _TABLE, ["cohort"])


def downgrade() -> None:
    """Structurally supported, semantically lossy.

    Dropping these columns discards the cohort, provenance and policy of
    every manually entered price. A re-upgrade backfills cohort to
    `fixture` and mode to UNKNOWN_LEGACY, so a price entered under the
    official forward test becomes indistinguishable from a fixture. That is
    the safe direction to lose information in, but it is still lost.
    """
    op.drop_index(f"ix_{_TABLE}_cohort", table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        for name in (
            f"ck_{_TABLE}_decimal_odds_range",
            f"ck_{_TABLE}_break_even_range",
            f"ck_{_TABLE}_provider_mode_vocabulary",
            f"ck_{_TABLE}_cohort_vocabulary",
        ):
            batch.drop_constraint(name, type_="check")
        for col in (
            "break_even_probability", "decimal_odds", "code_commit",
            "policy_version", "provider_mode", "cohort",
        ):
            batch.drop_column(col)
