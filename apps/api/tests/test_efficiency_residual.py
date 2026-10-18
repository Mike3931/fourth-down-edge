import pickle
from dataclasses import replace
from datetime import timedelta

import pytest

from fde_api.backtest.efficiency_study import lagged_stats
from fde_api.backtest.opponent_study import cutoff
from fde_api.features.history import LeagueHistory
from fde_api.models_ml.efficiency_residual import COLUMNS, KEYS, EfficiencyResidual
from fde_api.models_ml.protocol import MarketRef
from fde_api.pit.guards import LookaheadError


@pytest.fixture()
def inputs(league_session):
    h = LeagueHistory.load(league_session)
    rows = [
        {
            "game_id": g.id,
            "team_id": team,
            "observed_at": g.result_observed_at.isoformat(),
            "metrics": {k: float(g.home_score if team == g.home else g.away_score) for k in KEYS},
        }
        for g in h.games
        for team in (g.home, g.away)
    ]
    features = lagged_stats(h.games, rows, 365.0)
    for f in features.values():
        f.update(base_margin=2.0, base_total=44.0, base_sigma_margin=13.5, base_sigma_total=14.0)
    return h, rows, features


def test_lagged_stats_excludes_own_future_and_late_receipts(inputs):
    h, rows, features = inputs
    target = h.games[-1]
    poisoned = [dict(r, metrics=dict.fromkeys(KEYS, 99999.0)) if r["game_id"] == target.id else r for r in rows]
    original = lagged_stats(h.games, rows, 365.0)
    assert lagged_stats(h.games, poisoned, 365.0)[target.id] == original[target.id]
    delayed = [dict(r, observed_at=(target.kickoff + timedelta(days=10)).isoformat()) for r in rows]
    assert all(lagged_stats(h.games, delayed, 365.0)[target.id][k] is None for k in COLUMNS)
    with pytest.raises(ValueError, match="Duplicate"):
        lagged_stats(h.games, [*rows, rows[0]], 365.0)


def test_canonical_and_disputed_stats_fail_closed(inputs):
    h, rows, _ = inputs
    with pytest.raises(ValueError, match="Noncanonical"):
        lagged_stats(h.games, [dict(rows[0], team_id="NOT_A_PARTICIPANT")], 365.0)
    assert all(
        lagged_stats(h.games, [dict(r, result_disputed=True) for r in rows], 365.0)[h.games[-1].id][k] is None
        for k in COLUMNS
    )


def test_train_only_scaling_and_artifact_reproduction(inputs):
    h, _, features = inputs
    year = max(g.season for g in h.games)
    train = [g for g in h.games if g.season < year]
    target = h.games[-1]
    model = EfficiencyResidual()
    model.fit(train, features, h)
    prediction = model.predict(target, features[target.id])
    assert pickle.loads(pickle.dumps(model)).predict(target, features[target.id]) == prediction
    changed = dict(features)
    changed[target.id] = dict.fromkeys(COLUMNS, 99999.0) | features[target.id]
    # Poison future targets as well as their feature values: neither may enter fitting.
    future = [replace(g, home_score=80) if g.season == year else g for g in h.games]
    other = EfficiencyResidual()
    other.fit(train, changed, LeagueHistory(future, []))
    assert other.predict(target, features[target.id]) == prediction
    assert model.predict(target, features[target.id], MarketRef(-100, 100, -999, 999)) == prediction


def test_missing_stats_fall_back_and_disputes_refuse(inputs):
    h, _, features = inputs
    year = max(g.season for g in h.games)
    target = h.games[-1]
    model = EfficiencyResidual()
    model.fit([g for g in h.games if g.season < year], features, h)
    missing = features[target.id] | dict.fromkeys(COLUMNS)
    prediction = model.predict(target, missing)
    assert prediction.mu_margin == 2 and prediction.mu_total == 44
    assert prediction.sigma_margin == 13.5
    assert prediction.components["fallback_to_ratings"] == 1
    for flag in ("identity_review", "result_disputed"):
        with pytest.raises(ValueError, match="Unreviewed"):
            model.predict(target, features[target.id] | {flag: True})
    with pytest.raises(LookaheadError):
        model.predict(target, features[target.id] | {"as_of": cutoff(train_game := h.games[0]).isoformat()})
    assert train_game.id in model.training_ids
