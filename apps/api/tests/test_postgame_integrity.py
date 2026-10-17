# ruff: noqa: F811
"""Independent negative-path audit. All data here are controlled fixtures."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, event, select

from fde_api.db.models import ForecastFreeze, ModelEvaluation, ModelVersion, Prediction
from fde_api.forward.prospective import evaluate_forecasts, read_prospective
from test_prospective import GAME, NOW, cohort, generate_forecasts, schedule  # noqa: F401


@pytest.mark.parametrize(
    "fault",
    [
        "in_progress",
        "missing_score",
        "wrong_game",
        "swapped_teams",
        "late_forecast",
        "missing_freeze",
        "modified_hash",
        "missing_model",
        "stale_provider",
        "unresolved",
    ],
)
def test_unsafe_evaluation_is_blocked(cohort, fault):
    s = cohort
    generate_forecasts(s, now=NOW)
    s.commit()
    t = NOW + timedelta(days=3)
    final = schedule(s, status="FINAL", at=t, summary="final 24-17 from nflverse")
    if fault == "in_progress":
        final.game_status = "IN_PROGRESS"
    if fault == "missing_score":
        final.change_summary = "no verified score"
    if fault == "wrong_game":
        final.canonical_game_id = "different-game"
    if fault == "swapped_teams":
        final.home_team_id, final.away_team_id = "SEA", "DEN"
    if fault == "stale_provider":
        final.observed_at = NOW - timedelta(days=1)
    if fault == "unresolved":
        from fde_api.db.forward_models import GameIdentityReview

        s.add(
            GameIdentityReview(
                id="review",
                data_mode="LIVE_RESEARCH",
                provider="nflverse",
                provider_game_id=GAME,
                reason="UNRESOLVED",
                evidence={"canonical_game_id": GAME},
                created_at=NOW,
            )
        )
    for p in s.scalars(select(Prediction)):
        if fault == "late_forecast":
            p.created_at = p.as_of_at = t
        if fault == "missing_freeze":
            s.delete(s.get(ForecastFreeze, p.id))
        if fault == "modified_hash":
            p.outputs = {**p.outputs, "expected_margin": 999}
        if fault == "missing_model":
            m = s.get(ModelVersion, p.model_version_id)
            m.artifact_hash = None
    s.flush()
    assert evaluate_forecasts(s, now=t)["evaluations_added"] == 0
    assert read_prospective(s, now=t)["evaluated_forecasts"] == 0


def test_independent_metrics_and_idempotency(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    t = NOW + timedelta(days=3)
    schedule(s, status="FINAL", at=t, summary="final 24-17 from nflverse")
    assert evaluate_forecasts(s, now=t)["evaluations_added"] == 3
    s.commit()
    original = {p.id: (dict(p.outputs), p.created_at) for p in s.scalars(select(Prediction))}
    for e in s.scalars(select(ModelEvaluation)):
        p = s.get(Prediction, e.metrics["prediction_id"])
        o = p.outputs
        m = e.metrics
        assert m["brier"] == pytest.approx((o["home_win_prob"] - 1) ** 2)
        assert m["margin_signed_error"] == pytest.approx(o["expected_margin"] - 7)
        assert m["total_signed_error"] == pytest.approx(o["expected_total"] - 41)
        assert m["margin_absolute_error"] == pytest.approx(abs(o["expected_margin"] - 7))
    assert evaluate_forecasts(s, now=t)["evaluations_added"] == 0
    assert original == {p.id: (dict(p.outputs), p.created_at) for p in s.scalars(select(Prediction))}
    assert all(r["evaluation_state"] == "EVALUATED" for r in read_prospective(s, now=t)["records"])


def test_partial_evaluation_failure_rolls_back_and_retry_recovers(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    t = NOW + timedelta(days=3)
    schedule(s, status="FINAL", at=t, summary="final 24-17 from nflverse")
    s.commit()

    def fail(mapper, connection, target):
        raise RuntimeError("controlled interruption during evaluation write")

    event.listen(ModelEvaluation, "after_insert", fail)
    try:
        with pytest.raises(RuntimeError):
            evaluate_forecasts(s, now=t)
        s.rollback()
    finally:
        event.remove(ModelEvaluation, "after_insert", fail)
    assert list(s.scalars(select(ModelEvaluation))) == []
    assert len(list(s.scalars(select(Prediction)))) == 3
    assert evaluate_forecasts(s, now=t)["evaluations_added"] == 3


def test_pending_and_missing_attestation_ui_states(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    assert all(r["evaluation_state"] == "PREGAME" for r in read_prospective(s, now=NOW)["records"])
    t = NOW + timedelta(days=3)
    schedule(s, status="FINAL", at=t, summary="final 24-17 from nflverse")
    assert all(
        r["evaluation_state"] == "FINAL - PENDING EVALUATION" for r in read_prospective(s, now=t)["records"]
    )
    s.execute(delete(ForecastFreeze))
    s.flush()
    assert all(r["evaluation_state"] == "BLOCKED" for r in read_prospective(s, now=t)["records"])


def test_migrated_database_concurrency_uniqueness_and_freeze_guards(cohort, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session

    from fde_api.db.forward_models import ScheduleObservation
    from fde_api.db.models import Game, Team
    from fde_api.forward.workspace import initialize_workspace

    target = tmp_path / "race.db"
    initialize_workspace(target, purpose="PROSPECTIVE")
    engine = create_engine("sqlite:///" + target.as_posix())
    t = NOW + timedelta(days=3)
    with Session(engine) as s:
        for model in (Team, Game, ScheduleObservation):
            for r in cohort.scalars(select(model)):
                s.add(model(**{c.name: getattr(r, c.name) for c in model.__table__.columns}))
            s.flush()
        generate_forecasts(s, now=NOW)
        schedule(s, status="FINAL", at=t, summary="final 24-17 from nflverse")
        s.commit()
    barrier = Barrier(2)

    def run():
        with Session(engine) as s:
            barrier.wait()
            try:
                result = evaluate_forecasts(s, now=t)
                s.commit()
                return result["evaluations_added"]
            except DBAPIError:
                s.rollback()
                return 0  # SQLite loser can retry after the winner commits.

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(lambda _: run(), range(2))) == 3
    with Session(engine) as s:
        assert evaluate_forecasts(s, now=t)["evaluations_added"] == 0
        rows = list(s.scalars(select(ModelEvaluation)))
        assert len(rows) == 3
        duplicate = {c.name: getattr(rows[0], c.name) for c in ModelEvaluation.__table__.columns}
        duplicate["id"] = "different-id-same-prediction-result"
        s.add(ModelEvaluation(**duplicate))
        with pytest.raises(DBAPIError):
            s.flush()
        s.rollback()
    for statement in ("UPDATE forecast_freezes SET graph_hash='tampered'", "DELETE FROM forecast_freezes"):
        with pytest.raises(DBAPIError, match="immutable"), engine.begin() as c:
            c.execute(text(statement))
