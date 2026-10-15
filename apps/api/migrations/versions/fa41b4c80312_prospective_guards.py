"""Explicit prospective capability; immutable scoped existing records. No backfill."""

import sqlalchemy as sa
from alembic import op

revision = "fa41b4c80312"
down_revision = "f13b2a9c8041"
branch_labels = None
depends_on = None

PREDICATES = {
    "predictions": "OLD.horizon LIKE 'PROS_%'",
    "feature_snapshots": "OLD.feature_set = 'prospective-scores-v1'",
    "model_versions": "OLD.name LIKE 'pros-%'",
    "model_evaluations": "OLD.scope LIKE 'prospective:%'",
    "research_workspace": "1=1",
}


def upgrade() -> None:
    op.add_column(
        "research_workspace",
        sa.Column("purpose", sa.String(24), server_default="CAPTURE_ONLY", nullable=False),
    )
    dialect = op.get_bind().dialect.name
    for table, predicate in PREDICATES.items():
        if dialect == "postgresql":
            op.execute(f"""CREATE FUNCTION guard_pros_{table}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF {predicate} THEN RAISE EXCEPTION 'prospective evidence is immutable'; END IF;
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END; $$""")
            op.execute(
                f"CREATE TRIGGER guard_pros_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION guard_pros_{table}()"
            )
        elif dialect == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER guard_pros_{table}_{action.lower()} BEFORE {action} ON {table} WHEN {predicate} BEGIN SELECT RAISE(ABORT, 'prospective evidence is immutable'); END"
                )


def downgrade() -> None:
    for table in PREDICATES:
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER guard_pros_{table} ON {table}")
            op.execute(f"DROP FUNCTION guard_pros_{table}()")
        elif op.get_bind().dialect.name == "sqlite":
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER guard_pros_{table}_{action}")
    op.drop_column("research_workspace", "purpose")
