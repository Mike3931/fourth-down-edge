"""Model A — naive baselines. They exist to prove the pipeline and to be
beaten; none is a deployable recommendation source."""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments

_FALLBACK_SIGMA_MARGIN = 13.5
_FALLBACK_SIGMA_TOTAL = 10.0


def _completed(games: list[GameRow]) -> list[GameRow]:
    return [g for g in games if g.home_score is not None and g.away_score is not None]


class HomeFieldBaseline(GamePredictor):
    """Every game: margin = historical mean home margin, total = league mean."""

    name = "naive-homefield-v1"

    def fit(self, train_games, features, history, market=None) -> None:  # type: ignore[override]
        done = _completed(train_games)
        margins = [g.home_score - g.away_score for g in done]  # type: ignore[operator]
        totals = [g.home_score + g.away_score for g in done]  # type: ignore[operator]
        self.mu_margin = mean(margins) if margins else 2.0
        self.sigma_margin = pstdev(margins) if len(margins) > 1 else _FALLBACK_SIGMA_MARGIN
        self.mu_total = mean(totals) if totals else 44.0
        self.sigma_total = pstdev(totals) if len(totals) > 1 else _FALLBACK_SIGMA_TOTAL

    def predict(self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None):
        return PredictedMoments(self.mu_margin, self.sigma_margin, self.mu_total, self.sigma_total)


class RollingAverageBaseline(GamePredictor):
    """Margin/total from each team's simple points-for/against average over
    its last `window` completed games known at the cutoff (via history)."""

    name = "naive-rolling-v1"

    def __init__(self, window: int = 5) -> None:
        self.window = window

    def fit(self, train_games, features, history: LeagueHistory, market=None) -> None:  # type: ignore[override]
        self.history = history
        done = _completed(train_games)
        margins = [g.home_score - g.away_score for g in done]  # type: ignore[operator]
        totals = [g.home_score + g.away_score for g in done]  # type: ignore[operator]
        self.home_edge = mean(margins) if margins else 2.0
        self.sigma_margin = pstdev(margins) if len(margins) > 1 else _FALLBACK_SIGMA_MARGIN
        self.sigma_total = pstdev(totals) if len(totals) > 1 else _FALLBACK_SIGMA_TOTAL
        self.league_total = mean(totals) if totals else 44.0

    def _team_ppg(self, team: str, game: GameRow) -> tuple[float | None, float | None]:
        from fde_api.pit.clock import PredictionHorizon, horizon_as_of

        as_of = horizon_as_of(game.kickoff, PredictionHorizon.PREGAME)
        rows = self.history.rows_for(team, as_of, game.id)[-self.window :]
        pf: list[float] = []
        pa: list[float] = []
        for r in rows:
            g = self.history.by_id[r.game_id]
            if g.home_score is None or g.away_score is None:
                continue
            own = g.home_score if r.is_home else g.away_score
            opp = g.away_score if r.is_home else g.home_score
            pf.append(own)
            pa.append(opp)
        return (mean(pf) if pf else None), (mean(pa) if pa else None)

    def predict(self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None):
        h_pf, h_pa = self._team_ppg(game.home, game)
        a_pf, a_pa = self._team_ppg(game.away, game)
        if None in (h_pf, h_pa, a_pf, a_pa):
            mu_margin, mu_total = self.home_edge, self.league_total
        else:
            exp_home = (h_pf + a_pa) / 2  # type: ignore[operator]
            exp_away = (a_pf + h_pa) / 2  # type: ignore[operator]
            mu_margin = exp_home - exp_away + self.home_edge
            mu_total = exp_home + exp_away
        return PredictedMoments(mu_margin, self.sigma_margin, mu_total, self.sigma_total)
