"""Model D — transparent statistical baseline (ridge regression on
nfl-core-v1 features) and Model E — market-residual variant.

Both are deliberately linear with strong regularization, fitted with
proper predictive objectives (squared error on margin/total; the shared
Normal-discretization distribution supplies probabilities). Neither is
optimized against betting ROI.

Model E targets   actual_margin − market_implied_margin  and
                  actual_total  − market_total
— i.e. it asks whether football features carry information the closing
market hasn't priced. Its predicted moments are market mu + predicted
residual, with sigma from residual-model errors.
"""

from __future__ import annotations

from statistics import pstdev
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments

# Numeric snapshot keys used as regressors (order fixed for reproducibility).
FEATURE_COLUMNS: tuple[str, ...] = (
    "home_epa_per_play_adj",
    "home_epa_per_dropback_adj",
    "home_rush_epa_per_play_adj",
    "home_success_rate_adj",
    "home_early_down_epa_adj",
    "home_neutral_epa_adj",
    "home_explosive_rate_adj",
    "home_sack_rate_adj",
    "home_int_rate_adj",
    "home_def_epa_allowed",
    "home_pace_plays",
    "home_expected_possessions",
    "home_special_teams_epa",
    "home_red_zone_td_rate",
    "home_sos_epa",
    "home_uncertainty",
    "home_qb_pass_eff",
    "home_qb_sack_avoidance",
    "away_epa_per_play_adj",
    "away_epa_per_dropback_adj",
    "away_rush_epa_per_play_adj",
    "away_success_rate_adj",
    "away_early_down_epa_adj",
    "away_neutral_epa_adj",
    "away_explosive_rate_adj",
    "away_sack_rate_adj",
    "away_int_rate_adj",
    "away_def_epa_allowed",
    "away_pace_plays",
    "away_expected_possessions",
    "away_special_teams_epa",
    "away_red_zone_td_rate",
    "away_sos_epa",
    "away_uncertainty",
    "away_qb_pass_eff",
    "away_qb_sack_avoidance",
    "home_rest_days",
    "away_rest_days",
    "away_travel_miles",
    "away_tz_change_hours",
)


def _design_row(values: dict[str, Any], means: np.ndarray | None = None) -> np.ndarray:
    row = np.array(
        [float(values[c]) if values.get(c) is not None else np.nan for c in FEATURE_COLUMNS],
        dtype=float,
    )
    if means is not None:
        idx = np.isnan(row)
        row[idx] = means[idx]
    return row


class RidgeBaseline(GamePredictor):
    name = "glm-ridge-v1"

    def __init__(self, alpha: float = 25.0, seed: int = 20260801) -> None:
        self.alpha = alpha
        self.seed = seed

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "alpha": self.alpha, "features": list(FEATURE_COLUMNS), "seed": self.seed}

    def fit(self, train_games, features, history: LeagueHistory, market=None) -> None:  # type: ignore[override]
        xs, ym, yt = [], [], []
        for g in train_games:
            f = features.get(g.id)
            if f is None or g.home_score is None or g.away_score is None:
                continue
            xs.append(_design_row(f))
            ym.append(g.home_score - g.away_score)
            yt.append(g.home_score + g.away_score)
        x = np.vstack(xs)
        with np.errstate(all="ignore"):
            self.col_means = np.nanmean(x, axis=0)
        # A column with no observations at all imputes to 0 (z-scored center).
        self.col_means = np.nan_to_num(self.col_means, nan=0.0)
        inds = np.where(np.isnan(x))
        x[inds] = np.take(self.col_means, inds[1])
        self.col_std = x.std(axis=0)
        self.col_std[self.col_std == 0] = 1.0
        xz = (x - self.col_means) / self.col_std
        rng_state = np.random.RandomState(self.seed)
        self.m_margin = Ridge(alpha=self.alpha, random_state=rng_state).fit(xz, np.array(ym))
        self.m_total = Ridge(alpha=self.alpha, random_state=rng_state).fit(xz, np.array(yt))
        res_m = np.array(ym) - self.m_margin.predict(xz)
        res_t = np.array(yt) - self.m_total.predict(xz)
        self.sigma_margin = float(pstdev(res_m.tolist())) if len(res_m) > 30 else 13.5
        self.sigma_total = float(pstdev(res_t.tolist())) if len(res_t) > 30 else 10.0

    def predict(self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None):
        if features is None:
            raise ValueError(f"{self.name} requires a feature snapshot for {game.id}")
        row = _design_row(features, self.col_means)
        xz = ((row - self.col_means) / self.col_std).reshape(1, -1)
        return PredictedMoments(
            float(self.m_margin.predict(xz)[0]),
            self.sigma_margin,
            float(self.m_total.predict(xz)[0]),
            self.sigma_total,
        )


class MarketResidual(GamePredictor):
    name = "market-residual-v1"
    uses_market = True

    def __init__(self, alpha: float = 50.0, seed: int = 20260801) -> None:
        self.alpha = alpha
        self.seed = seed

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alpha": self.alpha,
            "target": "actual minus market-implied (margin & total)",
            "features": list(FEATURE_COLUMNS),
            "seed": self.seed,
        }

    def fit(self, train_games, features, history: LeagueHistory, market=None) -> None:  # type: ignore[override]
        market = market or {}
        xs, rm, rt = [], [], []
        for g in train_games:
            f, ref = features.get(g.id), market.get(g.id)
            if f is None or ref is None or g.home_score is None or g.away_score is None:
                continue
            if ref.home_line is None or ref.total_line is None:
                continue
            xs.append(_design_row(f))
            rm.append((g.home_score - g.away_score) - (-ref.home_line))
            rt.append((g.home_score + g.away_score) - ref.total_line)
        x = np.vstack(xs)
        with np.errstate(all="ignore"):
            self.col_means = np.nanmean(x, axis=0)
        # A column with no observations at all imputes to 0 (z-scored center).
        self.col_means = np.nan_to_num(self.col_means, nan=0.0)
        inds = np.where(np.isnan(x))
        x[inds] = np.take(self.col_means, inds[1])
        self.col_std = x.std(axis=0)
        self.col_std[self.col_std == 0] = 1.0
        xz = (x - self.col_means) / self.col_std
        self.m_margin = Ridge(alpha=self.alpha).fit(xz, np.array(rm))
        self.m_total = Ridge(alpha=self.alpha).fit(xz, np.array(rt))
        res_m = np.array(rm) - self.m_margin.predict(xz)
        res_t = np.array(rt) - self.m_total.predict(xz)
        self.sigma_margin = float(pstdev(res_m.tolist())) if len(res_m) > 30 else 13.5
        self.sigma_total = float(pstdev(res_t.tolist())) if len(res_t) > 30 else 10.0

    def predict(self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None):
        if features is None or market is None or market.home_line is None or market.total_line is None:
            raise ValueError(f"{self.name} requires features and a market reference for {game.id}")
        row = _design_row(features, self.col_means)
        xz = ((row - self.col_means) / self.col_std).reshape(1, -1)
        resid_m = float(self.m_margin.predict(xz)[0])
        resid_t = float(self.m_total.predict(xz)[0])
        return PredictedMoments(
            -market.home_line + resid_m,
            self.sigma_margin,
            market.total_line + resid_t,
            self.sigma_total,
            {"predicted_margin_residual": resid_m, "predicted_total_residual": resid_t},
        )
