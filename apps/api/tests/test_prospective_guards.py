import json
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from fde_api.forward.workspace import authorize_database, initialize_workspace
from test_prospective import NOW, cohort, generate_forecasts  # noqa: F401


def test_fresh_workspace_capability_cannot_be_added_to_capture_or_frozen_profile(tmp_path):
    capture = tmp_path / "capture.db"
    initialize_workspace(capture)
    with pytest.raises(ValueError, match="prospective workspace"):
        authorize_database(capture, prospective=True)
    marker = capture.with_name(capture.name + ".research-workspace.json")
    data = json.loads(marker.read_text())
    data["purpose"] = "PROSPECTIVE"
    marker.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="prospective workspace"):
        authorize_database(capture, prospective=True)
    with sqlite3.connect(capture) as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE research_workspace SET purpose='PROSPECTIVE'")
    target = tmp_path / "prospective.db"
    initialize_workspace(target, purpose="PROSPECTIVE")
    authorize_database(target, prospective=True)
    with sqlite3.connect(target) as db:
        db.execute("INSERT INTO model_evaluations VALUES ('frozen','model','test','{}',1,'2026-10-13')")
    with pytest.raises(ValueError, match="Historical research"):
        authorize_database(target, prospective=True)


def assert_immutable(engine, session):
    # Full populated controlled prediction graph, not synthetic live performance.
    from sqlalchemy import select

    from fde_api.db.models import FeatureSnapshot, ModelEvaluation, ModelVersion, Prediction

    generate_forecasts(session, now=NOW)
    session.commit()
    prediction = session.scalars(select(Prediction)).first()
    session.add(
        ModelEvaluation(
            id="pros-guard-eval",
            model_version_id=prediction.model_version_id,
            scope="prospective:PROS_72H",
            metrics={},
            sample_size=1,
            created_at=NOW,
        )
    )
    session.commit()
    ids = {
        "predictions": prediction.id,
        "feature_snapshots": prediction.feature_snapshot_id,
        "model_versions": prediction.model_version_id,
        "model_evaluations": "pros-guard-eval",
    }
    columns = {
        "predictions": "outputs",
        "feature_snapshots": "values",
        "model_versions": "metrics",
        "model_evaluations": "metrics",
    }
    for table, pk in ids.items():
        for statement in (
            f'UPDATE {table} SET "{columns[table]}"=:value WHERE id=:id',
            f"DELETE FROM {table} WHERE id=:id",
        ):
            with pytest.raises(DBAPIError, match="immutable"), engine.begin() as c:
                c.execute(text(statement), {"id": pk, "value": "{}"})
    assert session.get(Prediction, prediction.id) is not None
    assert session.get(ModelVersion, prediction.model_version_id) is not None
    assert session.get(FeatureSnapshot, prediction.feature_snapshot_id) is not None


def test_real_sqlite_migration_guards_and_restart(cohort, tmp_path):  # noqa: F811
    target = tmp_path / "pros.db"
    initialize_workspace(target, purpose="PROSPECTIVE")
    engine = create_engine("sqlite:///" + target.as_posix())
    # Copy controlled seed rows without bypassing destination guards.
    from sqlalchemy import select

    from fde_api.db.forward_models import ScheduleObservation
    from fde_api.db.models import Game, Team

    with Session(engine) as s:
        for model in (Team, Game, ScheduleObservation):
            for row in cohort.scalars(select(model)):
                s.merge(model(**{col.name: getattr(row, col.name) for col in model.__table__.columns}))
            s.flush()
        assert_immutable(engine, s)
    engine.dispose()
    reopened = create_engine("sqlite:///" + target.as_posix())
    with reopened.connect() as c:
        assert c.execute(text("SELECT count(*) FROM predictions")).scalar() == 3
    authorize_database(target, prospective=True)
