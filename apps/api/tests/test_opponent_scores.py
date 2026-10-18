import pickle
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from fde_api.backtest.opponent_study import aligned_ratings, cutoff, raw_season, study
from fde_api.features.history import LeagueHistory
from fde_api.models_ml.opponent_scores import OpponentScores
from fde_api.models_ml.protocol import MarketRef
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.pit.guards import LookaheadError


@pytest.fixture()
def history(league_session):
    return LeagueHistory.load(league_session)


def test_future_self_and_missing_results_cannot_change_fit(history):
    game = history.games[-10]
    a = OpponentScores()
    a.fit_at(history.games, cutoff(game))
    poisoned = [
        replace(g, home_score=80, away_score=1) if g.kickoff >= game.kickoff else g for g in history.games
    ]
    b = OpponentScores()
    b.fit_at(poisoned, cutoff(game))
    assert a.predict(game) == b.predict(game)
    assert game.id not in a.training_ids
    hidden = [
        replace(g, result_observed_at=max(g.kickoff + timedelta(days=10), game.kickoff + timedelta(days=10)))
        for g in history.games
    ]
    with pytest.raises(ValueError, match="prior completed"):
        OpponentScores().fit_at(hidden, cutoff(game))


def test_permutation_repeated_fit_and_pickle_reproduce(history):
    game = history.games[-1]
    a = OpponentScores()
    a.fit_at(history.games, cutoff(game))
    b = OpponentScores()
    b.fit_at(list(reversed(history.games)), cutoff(game))
    assert np.array_equal(a.regression.coef_, b.regression.coef_)
    assert a.predict(game) == b.predict(game)
    assert pickle.loads(pickle.dumps(a)).predict(game) == a.predict(game)
    a.fit_at(history.games, cutoff(game))
    assert a.predict(game) == b.predict(game)


def test_training_cutoff_and_boundary_are_explicit(history):
    game = history.games[-1]
    boundary = replace(history.games[-10], result_observed_at=cutoff(game))
    games = [g for g in history.games if g.id != boundary.id] + [boundary]
    m = OpponentScores()
    m.fit_at(games, cutoff(game))
    assert boundary.id not in m.training_ids
    with pytest.raises(ValueError, match="explicit aware"):
        m.fit(history.games, {}, history)
    with pytest.raises(ValueError, match="Aware cutoff"):
        m.fit_at(games, cutoff(game).replace(tzinfo=None))
    with pytest.raises(LookaheadError):
        m.predict(history.games[0])


def test_invalid_identity_and_result_chronology_refused(history):
    g = history.games[-1]
    with pytest.raises(ValueError, match="Duplicate"):
        OpponentScores().fit_at([*history.games, history.games[0]], cutoff(g))
    bad = replace(history.games[0], result_observed_at=history.games[0].kickoff)
    with pytest.raises(LookaheadError, match="Malformed"):
        OpponentScores().fit_at([bad, *history.games[1:]], cutoff(g))
    m = OpponentScores()
    m.fit_at(history.games, cutoff(g))
    with pytest.raises(ValueError, match="matchup"):
        m.predict(replace(g, away=g.home))


def test_unknown_team_missingness_and_no_market_dependency(history):
    g = replace(history.games[-1], home="UNSEEN")
    m = OpponentScores()
    m.fit_at(history.games, cutoff(g))
    p = m.predict(g)
    assert p.components["home_history_missing"] == 1
    assert m.predict(g, market=MarketRef(-99, 99, -900, 900)) == p
    assert 0 < p.distribution().home_win_prob() < 1
    assert "starting_quarterback" in m.describe()["missing_inputs"]


def test_scale_requires_prior_finite_residuals():
    m = OpponentScores()
    for pairs in ([], [(float("nan"), 0)] * 40):
        with pytest.raises(ValueError, match="finite prior"):
            m.calibrate_scale(pairs, provenance="earlier only")
    m.calibrate_scale([(10.0, 20.0)] * 40, provenance="earlier season prequential")
    assert m.sigma_margin == 10 and m.sigma_total == 20


@pytest.mark.parametrize("alpha,half_life", [(0, 365), (25, float("inf")), (-1, 20), (2, 0)])
def test_invalid_hyperparameters(alpha, half_life):
    with pytest.raises(ValueError):
        OpponentScores(alpha, half_life)


def test_chronological_replay_ignores_future_target_and_preserves_baseline(history):
    season = max(g.season for g in history.games)
    test = [g for g in history.games if g.season == season]
    train = [g for g in history.games if g.season < season]
    a, b = DynamicRatings(), DynamicRatings()
    a.fit(train, {}, history)
    b.fit(train, {}, history)
    last = test[-1]
    poisoned = [replace(g, home_score=80) if g.id == last.id else g for g in test]
    assert aligned_ratings(a, test)[last.id] == aligned_ratings(b, poisoned)[last.id]
    x, _ = raw_season(history, season, 25, 365)
    shuffled, _ = raw_season(LeagueHistory(list(reversed(history.games)), []), season, 25, 365)
    assert x == shuffled


def test_study_refuses_duplicate_folds_and_future_evaluation(history, session):
    for seasons in ([2025, 2024], [2024, 2024], [2026], []):
        with pytest.raises(ValueError, match="ascending"):
            study(
                session,
                history,
                {},
                seasons=seasons,
                source_hash="fixture",
                run_id="test",
                now=history.games[-1].kickoff,
            )


def test_cli_refuses_existing_database_before_reading_or_migrating(tmp_path):
    target = tmp_path / "protected-study.db"
    target.write_bytes(b"must remain unchanged")
    script = Path(__file__).parents[1] / "scripts" / "study_opponent_scores.py"
    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database-file",
            str(target),
            "--licensed-games-file",
            str(tmp_path / "absent.csv"),
            "--source-receipt",
            str(tmp_path / "absent.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2 and "existing databases are never adopted" in run.stderr
    assert target.read_bytes() == b"must remain unchanged"


def test_cli_refuses_unverified_source_before_creating_workspace(tmp_path):
    target = tmp_path / "new.db"
    source = tmp_path / "games.csv"
    source.write_bytes(b"test fixture, never real research data")
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}")
    script = Path(__file__).parents[1] / "scripts" / "study_opponent_scores.py"
    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database-file",
            str(target),
            "--licensed-games-file",
            str(source),
            "--source-receipt",
            str(receipt),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2 and "Hash-verified licensed" in run.stderr
    assert not target.exists()
