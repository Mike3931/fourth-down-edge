"""Actual PostgreSQL migration guards, including raw SQL mutation attempts."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ScheduleObservation
from fde_api.db.models import Game, Team
from test_prospective import cohort  # noqa: F401
from test_prospective_guards import assert_immutable

pytest_plugins = ["pgconftest"]
pytestmark = pytest.mark.pg_identity


def test_prospective_records_immutable_in_postgresql(pg_engine,cohort):  # noqa: F811
    assert pg_engine.dialect.name=='postgresql'
    with Session(pg_engine) as s:
        for model in (Team,Game,ScheduleObservation):
            for row in cohort.scalars(select(model)):
                values={col.name:getattr(row,col.name) for col in model.__table__.columns}
                if model is ScheduleObservation:
                    values.pop('id')
                    s.add(model(**values))
                elif s.get(model,row.id) is None:
                    s.add(model(**values))
            s.flush()
        s.commit()
        assert_immutable(pg_engine,s)
