"""Isolated score-only research candidate; never wired into the live worker.

Ridge team offense/opponent defense effects, decayed by result receipt age.
Historical receipt times are approximations, not archived publication vintages.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments
from fde_api.pit.guards import LookaheadError

MISSING_INPUTS = (
    "official_availability",
    "starting_quarterback",
    "personnel",
    "play_efficiency",
    "pace",
    "weather",
    "travel",
)


class OpponentScores(GamePredictor):
    name = "opponent-scores-ridge-v1"

    def __init__(self, alpha: float = 25.0, half_life: float = 365.0) -> None:
        if not all(math.isfinite(x) and x > 0 for x in (alpha, half_life)):
            raise ValueError("Positive finite alpha and half-life required")
        self.alpha, self.half_life = alpha, half_life
        self.sigma_margin, self.sigma_total = 13.5, 14.0
        self.scale_provenance = "unvalidated fallback; must calibrate on earlier residuals"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alpha": self.alpha,
            "half_life_days": self.half_life,
            "algorithm": "ridge offense + opponent defense + home-field + league intercept",
            "lookback_days": 5 * 366,
            "missing_inputs": list(MISSING_INPUTS),
            "scale_provenance": self.scale_provenance,
        }

    def fit(
        self,
        train_games: list[GameRow],
        features: dict[str, dict[str, Any]],
        history: LeagueHistory,
        market: dict[str, MarketRef] | None = None,
    ) -> None:
        raise ValueError("Use fit_at with an explicit aware information cutoff")

    def fit_at(self, games: list[GameRow], cutoff: datetime, *, exclude_id: str = "") -> None:
        if cutoff.tzinfo is None:
            raise ValueError("Aware cutoff required")
        if len({g.id for g in games}) != len(games):
            raise ValueError("Duplicate canonical game IDs")
        for g in games:
            if g.result_observed_at and (
                g.result_observed_at.tzinfo is None
                or g.kickoff.tzinfo is None
                or g.result_observed_at <= g.kickoff
            ):
                raise LookaheadError(f"Malformed result availability: {g.id}")
        train = sorted(
            (
                g
                for g in games
                if g.id != exclude_id
                and g.home_score is not None
                and g.away_score is not None
                and g.result_observed_at
                and g.result_observed_at < cutoff
                and 0 < (cutoff - g.result_observed_at).total_seconds() / 86400 <= 5 * 366
            ),
            key=lambda g: (g.result_observed_at, g.id),
        )
        if len(train) < 32:
            raise ValueError("At least32 prior completed games required")
        if any(g.home == g.away or not g.home or not g.away for g in train):
            raise ValueError("Invalid canonical team matchup")
        self.teams = sorted({t for g in train for t in (g.home, g.away)})
        self.index = {t: i for i, t in enumerate(self.teams)}
        self.cutoff = cutoff
        self.training_ids = tuple(g.id for g in train)
        self.training_results = tuple(
            (g.id, g.result_observed_at.isoformat(), g.home_score, g.away_score)
            for g in train
            if g.result_observed_at
        )
        x, y, weights = [], [], []
        for g in train:
            age = (cutoff - g.result_observed_at).total_seconds() / 86400  # type: ignore[operator]
            for team, opponent, home, score in (
                (g.home, g.away, True, g.home_score),
                (g.away, g.home, False, g.away_score),
            ):
                x.append(self._row(team, opponent, home))
                y.append(score)
                weights.append(2 ** (-age / self.half_life))
        self.regression = Ridge(alpha=self.alpha).fit(
            np.asarray(x), np.asarray(y), sample_weight=np.asarray(weights)
        )

    def _row(self, team: str, opponent: str, home: bool) -> np.ndarray:
        n = len(self.teams)
        row = np.zeros(1 + 2 * n)
        row[0] = 0.5 if home else -0.5
        if team in self.index:
            row[1 + self.index[team]] = 1
        if opponent in self.index:
            row[1 + n + self.index[opponent]] = 1
        return row

    def predict(
        self, game: GameRow, features: dict[str, Any] | None = None, market: MarketRef | None = None
    ) -> PredictedMoments:
        if game.id in self.training_ids or self.cutoff >= game.kickoff:
            raise LookaheadError("Own result or invalid prediction cutoff")
        if game.home == game.away or not game.home or not game.away:
            raise ValueError("Invalid canonical team matchup")
        home, away = self.regression.predict(
            np.asarray([self._row(game.home, game.away, True), self._row(game.away, game.home, False)])
        )
        return PredictedMoments(
            float(home - away),
            self.sigma_margin,
            float(home + away),
            self.sigma_total,
            {
                "home_expected_score": float(home),
                "away_expected_score": float(away),
                "home_history_missing": float(game.home not in self.index),
                "away_history_missing": float(game.away not in self.index),
            },
        )

    def calibrate_scale(self, residuals: list[tuple[float, float]], *, provenance: str) -> None:
        if len(residuals) < 32 or not all(math.isfinite(x) for row in residuals for x in row):
            raise ValueError("At least32 finite prior out-of-sample residual pairs required")
        # RMS includes bias; never fit probability calibration on the test season.
        self.sigma_margin, self.sigma_total = (
            max(5.0, float(np.sqrt(np.mean(np.square([r[i] for r in residuals]))))) for i in (0, 1)
        )
        self.scale_provenance = provenance
