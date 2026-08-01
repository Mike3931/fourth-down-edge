"""Common predictor protocol for models A–E.

A model's whole job is to produce margin/total moments for a game using
only information observable at the snapshot cutoff; the shared
GameDistribution turns moments into every probability the app needs, so
no model can emit mutually inconsistent probabilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.distribution import GameDistribution, MuSigma


@dataclass(frozen=True)
class MarketRef:
    """Closing-benchmark reference for a game. Kept OUTSIDE feature
    snapshots on purpose: only the market model (B, the benchmark) and
    the residual model (E, which predicts value relative to the close)
    receive it, and every report that uses it says so."""

    home_line: float | None  # bookmaker home handicap (home covers if margin + line > 0)
    total_line: float | None
    home_ml: int | None
    away_ml: int | None


@dataclass(frozen=True)
class PredictedMoments:
    mu_margin: float
    sigma_margin: float
    mu_total: float
    sigma_total: float
    components: dict[str, float] | None = None

    def distribution(self) -> GameDistribution:
        return GameDistribution(
            MuSigma(self.mu_margin, self.sigma_margin), MuSigma(self.mu_total, self.sigma_total)
        )


class GamePredictor(ABC):
    """fit() sees only training games (the walk-forward driver guarantees
    their results were observable before every game it will predict);
    predict() receives the feature snapshot values built at the cutoff."""

    name: str

    uses_market: bool = False  # True only for the benchmark (B) and residual (E) models

    @abstractmethod
    def fit(
        self,
        train_games: list[GameRow],
        features: dict[str, dict[str, Any]],
        history: LeagueHistory,
        market: dict[str, MarketRef] | None = None,
    ) -> None: ...

    @abstractmethod
    def predict(
        self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None
    ) -> PredictedMoments: ...

    def describe(self) -> dict[str, Any]:
        return {"name": self.name}
