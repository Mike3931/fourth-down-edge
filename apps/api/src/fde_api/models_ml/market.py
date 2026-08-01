"""Model B — market-implied benchmark.

The strongest market-only opponent available from this source: closing
spread → expected margin, closing total → expected total, no-vig
moneyline → win probability. Sigmas are estimated from historical
close-vs-actual residuals in the training window, so the benchmark's
distribution honestly reflects how noisy closing lines are.

This model appears in every comparison report; a football model that
cannot approach it has no business near a bankroll.
"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments


def american_to_prob(american: int) -> float:
    if american < 0:
        return -american / (-american + 100.0)
    return 100.0 / (american + 100.0)


def no_vig_home_prob(home_ml: int, away_ml: int) -> float:
    ph, pa = american_to_prob(home_ml), american_to_prob(away_ml)
    return ph / (ph + pa)


class MarketBenchmark(GamePredictor):
    name = "market-benchmark-v1"
    uses_market = True

    def fit(self, train_games, features, history: LeagueHistory, market=None) -> None:  # type: ignore[override]
        market = market or {}
        margin_res: list[float] = []
        total_res: list[float] = []
        for g in train_games:
            if g.home_score is None or g.away_score is None:
                continue
            ref = market.get(g.id)
            if ref is None:
                continue
            if ref.home_line is not None:
                margin_res.append((g.home_score - g.away_score) - (-ref.home_line))
            if ref.total_line is not None:
                total_res.append((g.home_score + g.away_score) - ref.total_line)
        self.sigma_margin = pstdev(margin_res) if len(margin_res) > 30 else 13.5
        self.sigma_total = pstdev(total_res) if len(total_res) > 30 else 10.0
        self.bias_margin = mean(margin_res) if len(margin_res) > 30 else 0.0
        self.bias_total = mean(total_res) if len(total_res) > 30 else 0.0

    def predict(self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None):
        if market is None or (market.home_line is None and market.total_line is None):
            raise ValueError(f"Market benchmark needs closing reference for {game.id}")
        mu_margin = (-market.home_line if market.home_line is not None else 0.0) + self.bias_margin
        mu_total = (market.total_line if market.total_line is not None else 44.0) + self.bias_total
        components = {}
        if market.home_ml is not None and market.away_ml is not None:
            components["no_vig_home_prob"] = no_vig_home_prob(market.home_ml, market.away_ml)
        return PredictedMoments(mu_margin, self.sigma_margin, mu_total, self.sigma_total, components)
