import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fde_api.backtest.research_comparison import (
    VERSION,
    clean,
    read_comparison,
    run_comparison,
    score_history,
    validation_status,
)
from fde_api.config import settings
from fde_api.db.models import BacktestRun, ModelEvaluation, ModelVersion, OddsSnapshot, Prediction
from fde_api.features.history import LeagueHistory
from fde_api.models_ml.ratings import DynamicRatings

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def test_refuses_small_samples_and_poor_market_performance():
    assert validation_status({"n": 0}, {})[0] == "INSUFFICIENT EVIDENCE"
    baseline = {"log_loss": .69, "brier": .25, "mae_margin": 12, "mae_total": 12}
    metrics = {"n": 600, "log_loss": .64, "brier": .22, "mae_margin": 11, "mae_total": 11,
        "interval_80_coverage": {"margin": .80, "total": .81},
        "closing_benchmark": {"n": 600, "paired_log_loss_delta_ci90": [.01, .03],
            "margin_mae": 10, "total_mae": 10,
            "candidate_margin_mae_same_sample": 11, "candidate_total_mae_same_sample": 11}}
    status, reasons = validation_status(metrics, baseline)
    assert status == "VALIDATION FAILED"
    assert len(reasons) == 3
    metrics["closing_benchmark"].update(paired_log_loss_delta_ci90=[-.03, -.01],
        margin_mae=12, total_mae=12)
    assert validation_status(metrics, baseline)[0] == "INSUFFICIENT EVIDENCE"
    assert "prospective" in validation_status(metrics, baseline)[1][0]
    assert clean({"x": float("nan"), "y": float("inf")}) == {"x": None, "y": None}


def test_read_is_cutoff_scoped_stale_and_unknown_versions_fail_closed(session):
    assert read_comparison(session, NOW)["report"] is None
    report = {"created_at": NOW.isoformat(), "estimates": [
        {"canonical_game_id": "A"}, {"canonical_game_id": "B"}]}
    session.add(BacktestRun(id="report", config={"comparison_version": VERSION},
        status="finished", metrics=report, created_at=NOW))
    session.flush()
    assert read_comparison(session, NOW - timedelta(seconds=1))["report"] is None
    # An arbitrary id in a stored JSON document cannot create a real-game forecast.
    assert read_comparison(session, NOW, "A")["report"]["estimates"] == []
    assert read_comparison(session, NOW + timedelta(days=2), "A")["report"]["estimates"] == []
    assert session.get(BacktestRun, "report").metrics == report
    session.add(BacktestRun(id="unknown", config={"comparison_version": "unknown"},
        status="finished", metrics=report, created_at=NOW + timedelta(seconds=1)))
    session.flush()
    assert read_comparison(session, NOW + timedelta(seconds=2))["report"]["created_at"] == NOW.isoformat()


def test_run_preserves_versions_predictions_and_excludes_closing_inputs(league_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path)
    first = run_comparison(league_session, test_seasons=[2024], source_hash="real-test-fixture", now=NOW)
    league_session.commit()
    original = {r.id: (r.model_version_id, r.outputs, r.created_at)
                for r in league_session.scalars(select(Prediction))}
    versions = {r.id: r.artifact_hash for r in league_session.scalars(select(ModelVersion))}
    # Deliberately corrupt the diagnostic closing yardstick. Candidate outcomes
    # must remain identical, while the market comparison changes.
    for quote in league_session.scalars(select(OddsSnapshot)):
        if quote.market == "MONEYLINE":
            quote.home_price_american = -999
            quote.away_price_american = 999
    second = run_comparison(league_session, test_seasons=[2024], source_hash="changed-yardstick", now=NOW)
    league_session.flush()
    for row in league_session.scalars(select(Prediction)):
        if row.id in original:
            assert (row.model_version_id, row.outputs, row.created_at) == original[row.id]
    assert all(league_session.get(ModelVersion, key).artifact_hash == value for key, value in versions.items())
    for a, b in zip(first["models"], second["models"], strict=True):
        assert a["metrics"]["log_loss"] == b["metrics"]["log_loss"]
        assert a["metrics"]["mae_margin"] == b["metrics"]["mae_margin"]
    assert first["registered_models"] != second["registered_models"]
    assert all(v.approval_status == "research_only" for v in league_session.scalars(select(ModelVersion)))
    assert len(list(league_session.scalars(select(ModelEvaluation)))) == 6
    assert all(p.horizon == "RETROSPECTIVE_KICKOFF" for p in league_session.scalars(select(Prediction)))


@pytest.mark.parametrize("seasons", [[], [2025, 2024], [2024, 2024]])
def test_invalid_fold_selection_refused(session, seasons):
    with pytest.raises(ValueError, match="Distinct ascending"):
        run_comparison(session, test_seasons=seasons, source_hash="x", now=NOW)


def test_cli_refuses_existing_database_before_loading_or_migrating(tmp_path):
    database = tmp_path / "renamed-research.db"
    original = b"existing research must remain unchanged"
    database.write_bytes(original)
    script = Path(__file__).parents[1] / "scripts" / "compare_research_models.py"
    result = subprocess.run([sys.executable, str(script), "--database-file", str(database)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "existing databases are never adopted" in result.stderr
    assert database.read_bytes() == original


def test_simultaneous_training_is_independent_of_database_order(league_session):
    history = LeagueHistory.load(league_session)
    forward = score_history(history)
    reverse = score_history(LeagueHistory(list(reversed(history.games)), []))
    assert [g.id for g in forward.games] == [g.id for g in reverse.games]
    assert forward.games == sorted(forward.games, key=lambda g: (g.kickoff, g.id))
    a, b = DynamicRatings(), DynamicRatings()
    a.fit(forward.games, {}, forward)
    b.fit(reverse.games, {}, reverse)
    assert a.r == b.r and a.hfa == b.hfa and a.s == b.s
