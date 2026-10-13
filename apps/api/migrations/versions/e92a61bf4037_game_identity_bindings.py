"""Add empty identity/isolated-workspace tables. No historical backfill or remapping."""
import sqlalchemy as sa
from alembic import op

revision = "e92a61bf4037"
down_revision = "d8f41c6a3b92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("game_identity_bindings",
        sa.Column("data_mode", sa.String(16), primary_key=True),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("provider_game_id", sa.String(64), primary_key=True),
        sa.Column("canonical_game_id", sa.String(32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_game_identity_bindings_canonical_game_id", "game_identity_bindings", ["canonical_game_id"])
    op.create_table("game_identity_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_game_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("research_workspace",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("research_workspace")
    op.drop_table("game_identity_reviews")
    op.drop_index("ix_game_identity_bindings_canonical_game_id", table_name="game_identity_bindings")
    op.drop_table("game_identity_bindings")
