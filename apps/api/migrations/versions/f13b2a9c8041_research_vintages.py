"""Empty append-only research tables; no legacy backfill or identity resolution."""
import sqlalchemy as sa
from alembic import op

revision = "f13b2a9c8041"
down_revision = "e92a61bf4037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dataset", sa.String(32), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(256), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False))
    op.create_index("ix_research_sources_dataset", "research_sources", ["dataset"])
    op.create_index("ix_research_sources_season", "research_sources", ["season"])
    op.create_table("research_facts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("research_sources.id"), nullable=False),
        sa.Column("team_id", sa.String(8)), sa.Column("player_id", sa.String(16)),
        sa.Column("week", sa.Integer()), sa.Column("canonical_game_id", sa.String(32)),
        sa.Column("values", sa.JSON(), nullable=False))
    for name in ("source_id", "team_id", "player_id"):
        op.create_index(f"ix_research_facts_{name}", "research_facts", [name])
    op.create_table("research_player_bindings",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(80), primary_key=True),
        sa.Column("player_id", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False))
    op.create_index("ix_research_player_bindings_player_id", "research_player_bindings", ["player_id"])
    op.create_table("research_player_reviews",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("player_id", sa.String(16)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False))
    op.create_index("ix_research_player_reviews_player_id", "research_player_reviews", ["player_id"])
    op.create_table("research_polls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dataset", sa.String(32), nullable=False), sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("research_sources.id")),
        sa.Column("error", sa.String(120)))
    op.create_index("ix_research_polls_dataset", "research_polls", ["dataset"])
    op.create_table("game_research_manifests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_game_id", sa.String(32), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False))
    op.create_index("ix_game_research_manifests_canonical_game_id", "game_research_manifests", ["canonical_game_id"])
    # Database protection in both accepted dialects, including raw SQL writes.
    tables = ("research_sources", "research_facts", "research_player_bindings", "research_player_reviews", "game_research_manifests")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE FUNCTION fde_research_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'research evidence is append-only'; END; $$")
        for table in tables:
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION fde_research_immutable()")
    elif op.get_bind().dialect.name == "sqlite":
        for table in tables:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'research evidence is append-only'); END")


def downgrade() -> None:
    for table in ("game_research_manifests", "research_polls", "research_player_reviews", "research_player_bindings", "research_facts", "research_sources"):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION fde_research_immutable()")
