"""End-to-end walk-forward on the synthetic league.

Verifies fold isolation, append-only retention of every status
(including PASS and DATA_INCOMPLETE), research-only statuses, registry
population, and that the market benchmark is present in results."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from fde_api.backtest.walkforward import WalkForwardConfig, run_walkforward
from fde_api.config import settings
from fde_api.db.models import (
    BacktestRecommendation,
    BacktestRun,
    ModelVersion,
    Prediction,
)


@pytest.fixture()
def wf_result(league_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    cfg = WalkForwardConfig(
        test_season=2025,
        half_life_grid=(365.0,),
        ridge_alpha_grid=(25.0,),
        ratings_k_grid=(0.12,),
        candidate_edge_grid=(0.04,),
    )
    res = run_walkforward(league_session, cfg)
    league_session.commit()
    return league_session, res


def test_fold_reported_correctly(wf_result) -> None:
    _, res = wf_result
    assert res["fold"] == {"train": "<= 2023", "validate": 2024, "test": 2025}


def test_all_models_evaluated_including_market_benchmark(wf_result) -> None:
    _, res = wf_result
    names = set(res["test_metrics"])
    assert "market-benchmark-v1" in names
    assert {"naive-homefield-v1", "team-ratings-v1", "glm-ridge-v1", "market-residual-v1"} <= names
    for m in res["test_metrics"].values():
        assert m["n"] > 0


def test_only_research_statuses_emitted(wf_result) -> None:
    s, res = wf_result
    statuses = set(
        s.scalars(select(BacktestRecommendation.status).distinct())
    )
    assert statuses <= {"RESEARCH_CANDIDATE", "WATCH", "PASS", "DATA_INCOMPLETE"}
    assert "BET" not in statuses


def test_passes_retained_append_only(wf_result) -> None:
    s, res = wf_result
    run_id = res["run_id"]
    rows = s.scalars(
        select(BacktestRecommendation).where(BacktestRecommendation.backtest_run_id == run_id)
    ).all()
    by_status = res["betting_simulation"]["statuses"]
    assert len(rows) == sum(by_status.values())
    assert by_status["PASS"] > 0  # passes are stored, not discarded


def test_registry_populated_research_only(wf_result) -> None:
    s, _ = wf_result
    models = s.scalars(select(ModelVersion)).all()
    assert len(models) >= 6
    assert all(m.approval_status == "research_only" for m in models)
    for m in models:
        assert m.code_commit and m.dependency_lock_hash
        assert m.known_limitations


def test_predictions_stored_for_test_season(wf_result) -> None:
    s, res = wf_result
    n = len(s.scalars(select(Prediction)).all())
    assert n == res["predictions_stored"] > 0


def test_run_row_finished(wf_result) -> None:
    s, res = wf_result
    run = s.get(BacktestRun, res["run_id"])
    assert run.status == "finished"
    assert run.metrics is not None
