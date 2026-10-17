"""Add immutable forecast attestations; no historical records are backfilled."""

import sqlalchemy as sa
from alembic import op

revision = "fb0100000001"
down_revision = "fa41b4c80312"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable columns preserve historical evaluations without rewriting them.
    op.add_column("model_evaluations", sa.Column("prediction_id", sa.String(96), nullable=True))
    op.add_column("model_evaluations", sa.Column("result_observation_id", sa.Integer(), nullable=True))
    op.create_index(
        "uq_evaluation_result", "model_evaluations", ["prediction_id", "result_observation_id"], unique=True
    )
    op.create_table(
        "forecast_freezes",
        sa.Column("prediction_id", sa.String(96), sa.ForeignKey("predictions.id"), primary_key=True),
        sa.Column("graph_hash", sa.String(64), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
    )
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER freeze_{action.lower()} BEFORE {action} ON forecast_freezes BEGIN SELECT RAISE(ABORT, 'forecast freeze is immutable'); END"
            )
    else:
        op.execute("""CREATE FUNCTION guard_forecast_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'forecast freeze is immutable'; END; $$""")
        op.execute(
            "CREATE TRIGGER guard_forecast_freeze BEFORE UPDATE OR DELETE ON forecast_freezes FOR EACH ROW EXECUTE FUNCTION guard_forecast_freeze()"
        )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM forecast_freezes")).scalar():
        raise RuntimeError("Forecast attestations must be retained; destructive downgrade refused")
    op.drop_table("forecast_freezes")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION guard_forecast_freeze()")
    op.drop_index("uq_evaluation_result", table_name="model_evaluations")
    op.drop_column("model_evaluations", "result_observation_id")
    op.drop_column("model_evaluations", "prediction_id")
