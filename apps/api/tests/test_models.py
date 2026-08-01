"""Model tests: reproducibility, sanity, serialization, market math."""

from __future__ import annotations

import contextlib
import math
import pickle

import pytest

from fde_api.backtest.walkforward import load_market_refs
from fde_api.features import LeagueHistory
from fde_api.models_ml.baselines import HomeFieldBaseline, RollingAverageBaseline
from fde_api.models_ml.glm import MarketResidual, RidgeBaseline
from fde_api.models_ml.market import MarketBenchmark, american_to_prob, no_vig_home_prob
from fde_api.models_ml.ratings import DynamicRatings


@pytest.fixture()
def league(league_session):
    h = LeagueHistory.load(league_session)
    market, _ = load_market_refs(league_session)
    train = [g for g in h.games if g.season <= 2023]
    test = [g for g in h.games if g.season == 2024]
    # tiny in-memory feature dict via the builder (subset for speed)
    from fde_api.features import CoreV1Builder
    from fde_api.pit.clock import PredictionHorizon

    b = CoreV1Builder(h)
    feats = {}
    for g in train[-80:] + test[:40]:
        with contextlib.suppress(Exception):
            feats[g.id] = b.build(g, PredictionHorizon.PREGAME).values
    return h, market, train, test, feats


def test_market_probability_math() -> None:
    assert abs(american_to_prob(-110) - 110 / 210) < 1e-12
    assert abs(american_to_prob(150) - 100 / 250) < 1e-12
    p = no_vig_home_prob(-110, -110)
    assert abs(p - 0.5) < 1e-12


def test_ridge_reproducible_with_seed(league) -> None:
    h, market, train, test, feats = league
    outs = []
    for _ in range(2):
        m = RidgeBaseline(alpha=25.0, seed=123)
        m.fit(train, feats, h)
        g = next(g for g in test if g.id in feats)
        outs.append(m.predict(g, feats[g.id]))
    assert outs[0].mu_margin == outs[1].mu_margin
    assert outs[0].mu_total == outs[1].mu_total


def test_no_nan_predictions(league) -> None:
    h, market, train, test, feats = league
    models = [HomeFieldBaseline(), RollingAverageBaseline(), DynamicRatings(), RidgeBaseline(), MarketResidual()]
    for m in models:
        m.fit(train, feats, h, market)
    for g in test[:20]:
        for m in models:
            try:
                pm = m.predict(g, feats.get(g.id), market.get(g.id) if m.uses_market else None)
            except ValueError:
                continue
            for v in (pm.mu_margin, pm.sigma_margin, pm.mu_total, pm.sigma_total):
                assert not math.isnan(v)
            assert pm.sigma_margin > 0 and pm.sigma_total > 0


def test_model_serialization_roundtrip(league) -> None:
    h, market, train, test, feats = league
    m = RidgeBaseline(alpha=25.0, seed=7)
    m.fit(train, feats, h)
    clone = pickle.loads(pickle.dumps(m))
    g = next(g for g in test if g.id in feats)
    assert clone.predict(g, feats[g.id]).mu_margin == m.predict(g, feats[g.id]).mu_margin


def test_ratings_learn_strength_order(league_session) -> None:
    h = LeagueHistory.load(league_session)
    m = DynamicRatings(k=0.15)
    m.fit([g for g in h.games if g.season <= 2024], {}, h)
    # Synthetic league: AAA strongest, HHH weakest.
    assert m.r["AAA"] > m.r["HHH"]


def test_market_benchmark_requires_reference(league) -> None:
    h, market, train, test, feats = league
    m = MarketBenchmark()
    m.fit(train, feats, h, market)
    g = test[0]
    with pytest.raises(ValueError, match="[Mm]arket"):
        m.predict(g, None, None)
