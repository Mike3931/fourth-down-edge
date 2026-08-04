"""The comparison tool is certification-critical, so it is tested like it.

Its first version keyed evaluations on (model, scope) and recommendations
on (game, market, selection, as_of) ACROSS the whole database. Both tables
are append-only per run, so after a rerun each key existed twice and a
plain dict assignment kept whichever row was encountered last. The tool
reported "identical" without ever having compared the two generations.

These tests exist so that cannot come back.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fde_api.db.models import (
    BacktestRecommendation,
    BacktestRun,
    Base,
    Game,
    ModelEvaluation,
    ModelVersion,
    Stadium,
    Team,
)

_spec = importlib.util.spec_from_file_location(
    "compare_runs", Path(__file__).resolve().parents[1] / "scripts" / "compare_runs.py"
)
assert _spec and _spec.loader
compare_runs = importlib.util.module_from_spec(_spec)
sys.modules["compare_runs"] = compare_runs
_spec.loader.exec_module(compare_runs)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
KICK = datetime(2025, 9, 7, 17, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.add_all([
        Team(id="BUF", name="Bills"),
        Team(id="KC", name="Chiefs"),
        Stadium(id="S1", name="Stadium"),
    ])
    s.add(ModelVersion(
        id="team-ratings-v1", name="ratings", target="margin", algorithm="elo",
        approval_status="research_only", feature_set="nfl-core-v1",
        calibration_version=None, train_window="", validation_window="",
        test_window="", hyperparameters={}, dataset_hashes={}, metrics={},
        known_limitations="", random_seed=1, code_commit="c", dependency_lock_hash="h", created_at=NOW,
    ))
    s.add(Game(
        id="G1", season=2025, week=1, game_type="REG", kickoff_utc=KICK,
        home_team_id="BUF", away_team_id="KC", stadium_id="S1",
        home_score=24, away_score=20,
        result_observed_at=KICK + timedelta(hours=4, minutes=30),
    ))
    s.commit()
    yield s
    s.close()


def _run(s, rid: str, season: int = 2025) -> None:
    s.add(BacktestRun(id=rid, config={"test_season": season}, status="finished",
                      metrics={}, created_at=NOW))
    s.commit()


def _eval(s, rid: str, model: str, brier: float, scope: str = "test:2025:PREGAME") -> None:
    s.add(ModelEvaluation(id=f"ev_{rid}_{model}", model_version_id=model, scope=scope,
                          metrics={"brier": brier, "log_loss": 0.6, "margin_mae": 10.0},
                          sample_size=285, created_at=NOW))
    s.commit()


def _rec(s, rid: str, status: str = "PASS", sel: str = "HOME", pnl: float = 1.0) -> None:
    s.add(BacktestRecommendation(
        backtest_run_id=rid, game_id="G1", prediction_id="p1", market="SPREAD",
        selection=sel, line=-3.0, price_american=-110, status=status,
        reasons={}, execution={"filled": True}, settlement={"pnl": pnl},
        as_of_at=KICK, created_at=NOW,
    ))
    s.commit()


class TestRunsCannotCollapse:
    def test_same_model_and_scope_in_two_runs_stay_separate(self, session) -> None:
        _run(session, "A")
        _run(session, "B")
        _eval(session, "A", "team-ratings-v1", 0.2158)
        _eval(session, "B", "team-ratings-v1", 0.2159)
        out = compare_runs.compare_runs(session, "A", "B")
        assert out["evaluations"]["rows_before"] == 1
        assert out["evaluations"]["rows_after"] == 1
        assert out["evaluations"]["difference_count"] == 1
        assert out["evaluations"]["by_key_prefix"] == {"team-ratings-v1": 1}

    def test_the_old_collapsed_key_method_would_hide_it(self, session) -> None:
        """Reproduces the original defect: keying across the whole database
        rather than per run. Both rows share a key, the last wins, and the
        difference vanishes. If this ever stops differing from the correct
        result, the tool has regressed."""
        _run(session, "A")
        _run(session, "B")
        _eval(session, "A", "team-ratings-v1", 0.2158)
        _eval(session, "B", "team-ratings-v1", 0.2159)

        from sqlalchemy import select

        collapsed: dict[str, object] = {}
        for e in session.scalars(select(ModelEvaluation)):
            collapsed[f"{e.model_version_id}|{e.scope}"] = e.metrics
        assert len(collapsed) == 1  # two runs, one entry — the defect

        correct = compare_runs.compare_runs(session, "A", "B")
        assert correct["evaluations"]["difference_count"] == 1


class TestExplicitSelection:
    def test_both_run_ids_are_required(self) -> None:
        import inspect

        sig = inspect.signature(compare_runs.compare_runs)
        for name in ("before_run_id", "after_run_id"):
            assert sig.parameters[name].default is inspect.Parameter.empty

    def test_a_missing_run_fails_clearly(self, session) -> None:
        _run(session, "A")
        with pytest.raises(compare_runs.ComparisonError, match="does not exist"):
            compare_runs.compare_runs(session, "A", "NOPE")

    def test_a_season_with_one_run_fails_clearly(self, session) -> None:
        _run(session, "A")
        with pytest.raises(compare_runs.ComparisonError, match="need two runs"):
            compare_runs.compare_season(session, 2025)


class TestDuplicatesAndShape:
    def test_duplicate_recommendations_in_one_run_fail(self, session) -> None:
        _run(session, "A")
        _run(session, "B")
        _rec(session, "A")
        _rec(session, "A")  # same semantic key twice
        _rec(session, "B")
        with pytest.raises(compare_runs.ComparisonError, match="duplicate recommendation"):
            compare_runs.compare_runs(session, "A", "B")

    def test_row_count_mismatch_is_reported_before_values(self, session) -> None:
        _run(session, "A")
        _run(session, "B")
        _rec(session, "A", sel="HOME")
        _rec(session, "A", sel="AWAY")
        _rec(session, "B", sel="HOME")
        out = compare_runs.compare_runs(session, "A", "B")
        assert any("row count differs" in p for p in out["shape_problems"])

    def test_unmatched_rows_are_listed(self, session) -> None:
        _run(session, "A")
        _run(session, "B")
        _rec(session, "A", sel="HOME")
        _rec(session, "A", sel="AWAY")
        _rec(session, "B", sel="HOME")
        out = compare_runs.compare_runs(session, "A", "B")
        assert out["recommendations"]["unmatched_before"]
        assert any("only in the first run" in p for p in out["shape_problems"])


class TestFieldCoverage:
    def test_every_evaluation_metric_is_compared(self, session) -> None:
        _run(session, "A")
        _run(session, "B")
        _eval(session, "A", "team-ratings-v1", 0.2)
        _eval(session, "B", "team-ratings-v1", 0.2)
        # perturb every metric on the B side
        from sqlalchemy import select

        e = session.scalars(select(ModelEvaluation).where(ModelEvaluation.id.like("ev_B_%"))).one()
        e.metrics = {"brier": 0.3, "log_loss": 0.7, "margin_mae": 11.0}
        session.commit()
        out = compare_runs.compare_runs(session, "A", "B")
        paths = {d["path"].split(".")[-1] for d in out["evaluations"]["sample"]}
        assert {"brier", "log_loss", "margin_mae"} <= paths

    @pytest.mark.parametrize("field,before,after", [
        ("status", "PASS", "RESEARCH_CANDIDATE"),
        ("line", -3.0, -3.5),
        ("price_american", -110, -120),
    ])
    def test_candidate_fields_are_compared(self, session, field, before, after) -> None:
        _run(session, "A")
        _run(session, "B")
        _rec(session, "A")
        _rec(session, "B")
        from sqlalchemy import select

        r = session.scalars(
            select(BacktestRecommendation).where(BacktestRecommendation.backtest_run_id == "B")
        ).one()
        setattr(r, field, after)
        session.commit()
        out = compare_runs.compare_runs(session, "A", "B")
        assert out["recommendations"]["difference_count"] >= 1
        assert any(field in d["path"] for d in out["recommendations"]["sample"])

    def test_execution_and_settlement_are_compared(self, session) -> None:
        """These carry the simulated fill, pnl, ROI and drawdown inputs."""
        _run(session, "A")
        _run(session, "B")
        _rec(session, "A", pnl=1.0)
        _rec(session, "B", pnl=2.0)
        out = compare_runs.compare_runs(session, "A", "B")
        assert any("settlement" in d["path"] for d in out["recommendations"]["sample"])


class TestSemanticKeysExcludeUnstableIds:
    def test_generated_ids_are_documented_as_excluded(self) -> None:
        assert "id" in compare_runs.EXCLUDED_FROM_SEMANTIC_KEYS
        assert "backtest_run_id" in compare_runs.EXCLUDED_FROM_SEMANTIC_KEYS

    def test_stable_keys_are_documented(self) -> None:
        assert compare_runs.EVALUATION_KEY == ("model_version_id", "scope")
        assert compare_runs.RECOMMENDATION_KEY == (
            "game_id", "market", "selection", "as_of_at",
        )

    def test_autoincrement_id_is_not_in_the_recommendation_key(self, session) -> None:
        """Two runs mint different primary keys for the same wager; keying
        on them would report every row as added-and-removed."""
        _run(session, "A")
        _run(session, "B")
        _rec(session, "A")
        _rec(session, "B")
        out = compare_runs.compare_runs(session, "A", "B")
        assert out["recommendations"]["difference_count"] == 0
        assert not out["recommendations"]["unmatched_before"]
        assert not out["recommendations"]["unmatched_after"]
