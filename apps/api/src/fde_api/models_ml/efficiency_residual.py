"""Research-only ridge correction to frozen, pregame dynamic-rating moments."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments
from fde_api.pit.guards import LookaheadError

KEYS = ("epa_per_play", "epa_per_dropback", "rush_epa_per_play", "success_rate", "int_rate", "plays")
COLUMNS = tuple(f"{mode}_{key}" for mode in ("off_diff", "def_diff", "off_sum", "def_sum") for key in KEYS)


class EfficiencyResidual(GamePredictor):
    name = "efficiency-residual-v1"

    def __init__(self, alpha: float = 100.0) -> None:
        if not np.isfinite(alpha) or alpha <= 0:
            raise ValueError("Positive finite regularization required")
        self.alpha = alpha
        self.sigma_margin, self.sigma_total = 13.5, 14.0
        self.scale_provenance = "fallback; prior-season OOS residual calibration required"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "algorithm": "standardized ridge residual on prior EPA and pace",
            "alpha": self.alpha,
            "features": list(COLUMNS),
            "base": "dynamic ratings",
            "scale_provenance": self.scale_provenance,
            "excluded": ["QB", "personnel", "official availability", "weather", "travel", "closing prices"],
        }

    def fit(
        self,
        train_games: list[GameRow],
        features: dict[str, dict[str, Any]],
        history: LeagueHistory,
        market: dict[str, MarketRef] | None = None,
    ) -> None:
        x, y, ids, received = [], [], [], []
        if len({g.id for g in train_games}) != len(train_games):
            raise ValueError("Duplicate canonical game IDs")
        for g in sorted(train_games, key=lambda g: (g.kickoff, g.id)):
            f = features.get(g.id)
            if not f or f.get("identity_review") or f.get("result_disputed"):
                continue
            if g.home_score is None or g.away_score is None:
                continue
            if g.result_observed_at is None:
                continue
            if g.result_observed_at <= g.kickoff:
                raise LookaheadError("Malformed training result availability")
            x.append([f.get(k, np.nan) if f.get(k) is not None else np.nan for k in COLUMNS])
            y.append(
                [
                    g.home_score - g.away_score - f["base_margin"],
                    g.home_score + g.away_score - f["base_total"],
                ]
            )
            ids.append(g.id)
            received.append(g.result_observed_at)
        if len(ids) < 32:
            raise ValueError("At least32 eligible prior training games required")
        self.training_ids = tuple(ids)
        self.max_training_result_at = max(received)
        matrix = np.asarray(x, dtype=float)
        valid = np.isfinite(matrix)
        count = valid.sum(axis=0)
        self.means = np.divide(
            np.where(valid, matrix, 0).sum(axis=0), count, out=np.zeros(len(COLUMNS)), where=count > 0
        )
        filled = np.where(valid, matrix, self.means)
        self.scale = filled.std(axis=0)
        self.scale[self.scale == 0] = 1
        self.regression = Ridge(alpha=self.alpha).fit((filled - self.means) / self.scale, np.asarray(y))

    def predict(
        self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None
    ) -> PredictedMoments:
        if features is None or features.get("identity_review") or features.get("result_disputed"):
            raise ValueError("Unreviewed identity/result or missing feature snapshot")
        if game.id in self.training_ids:
            raise LookaheadError("Own training result cannot predict itself")
        if self.max_training_result_at >= datetime.fromisoformat(features["as_of"]):
            raise LookaheadError("Training results overlap forecast cutoff")
        raw = np.asarray(
            [features.get(k) if features.get(k) is not None else np.nan for k in COLUMNS], dtype=float
        )
        missing = ~np.isfinite(raw)
        if missing.all():
            return PredictedMoments(
                features["base_margin"],
                features["base_sigma_margin"],
                features["base_total"],
                features["base_sigma_total"],
                {"missing_stat_features": float(missing.sum()), "fallback_to_ratings": 1.0},
            )
        filled = np.where(missing, self.means, raw)
        delta = self.regression.predict(((filled - self.means) / self.scale).reshape(1, -1))[0]
        return PredictedMoments(
            features["base_margin"] + float(delta[0]),
            self.sigma_margin,
            features["base_total"] + float(delta[1]),
            self.sigma_total,
            {
                "margin_correction": float(delta[0]),
                "total_correction": float(delta[1]),
                "missing_stat_features": float(missing.sum()),
                "fallback_to_ratings": 0.0,
            },
        )
