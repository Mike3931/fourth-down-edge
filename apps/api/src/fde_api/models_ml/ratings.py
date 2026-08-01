"""Model C — dynamic team-rating model (margin-Elo with decay).

Documented specification
------------------------
State: one rating R_t per team in *points* (0 = league average), plus a
scoring rating S_t (points contributed to totals), and a global
home-field advantage HFA.

Prediction:      margin_hat = R_home − R_away + HFA
                 total_hat  = S_home + S_away + league_base

Update after each completed game (chronological, results only become
usable at result_observed_at — enforced by the walk-forward driver
feeding games in observation order):

    err   = actual_margin − margin_hat
    R_home += k · err / 2 ;  R_away −= k · err / 2

    t_err = actual_total − total_hat
    S_home += k_t · t_err / 2 ;  S_away += k_t · t_err / 2

HFA adapts slowly:  HFA += k_hfa · err  (small k_hfa)

Priors / season transition: at a team's first game of a season its
rating is shrunk toward 0: R ← (1−λ)·R. Teams therefore carry
information across seasons without resetting to identical confidence.

Uncertainty: sigma comes from the training window's realized prediction
errors, widened for teams with few games in the current season by
factor (1 + widen / games_this_season).

Hyperparameters (k, k_t, λ) are selected on prior validation windows
only — never on the test season.
"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments


class DynamicRatings(GamePredictor):
    name = "team-ratings-v1"

    def __init__(self, k: float = 0.12, k_total: float = 0.10, season_shrink: float = 0.35) -> None:
        self.k = k
        self.k_total = k_total
        self.season_shrink = season_shrink
        self.hfa = 2.0
        self.k_hfa = 0.002

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "update": "R_home += k*err/2; R_away -= k*err/2; S += k_t*t_err/2; HFA += k_hfa*err",
            "priors": "season start shrinks ratings toward 0 by season_shrink; no full reset",
            "k": self.k,
            "k_total": self.k_total,
            "season_shrink": self.season_shrink,
        }

    def _reset(self) -> None:
        self.r: dict[str, float] = {}
        self.s: dict[str, float] = {}
        self.season_seen: dict[str, int] = {}
        self.games_in_season: dict[str, int] = {}
        self.league_base = 44.0
        self.errors: list[float] = []
        self.t_errors: list[float] = []

    def _pre_game(self, team: str, season: int) -> None:
        self.r.setdefault(team, 0.0)
        self.s.setdefault(team, 0.0)
        if self.season_seen.get(team) != season:
            self.r[team] *= 1 - self.season_shrink
            self.s[team] *= 1 - self.season_shrink
            self.season_seen[team] = season
            self.games_in_season[team] = 0

    def _observe(self, g: GameRow) -> None:
        if g.home_score is None or g.away_score is None:
            return
        self._pre_game(g.home, g.season)
        self._pre_game(g.away, g.season)
        margin_hat = self.r[g.home] - self.r[g.away] + self.hfa
        total_hat = self.s[g.home] + self.s[g.away] + self.league_base
        err = (g.home_score - g.away_score) - margin_hat
        t_err = (g.home_score + g.away_score) - total_hat
        self.errors.append(err)
        self.t_errors.append(t_err)
        self.r[g.home] += self.k * err / 2
        self.r[g.away] -= self.k * err / 2
        self.s[g.home] += self.k_total * t_err / 2
        self.s[g.away] += self.k_total * t_err / 2
        self.hfa += self.k_hfa * err
        self.games_in_season[g.home] = self.games_in_season.get(g.home, 0) + 1
        self.games_in_season[g.away] = self.games_in_season.get(g.away, 0) + 1

    def fit(self, train_games, features, history: LeagueHistory, market=None) -> None:  # type: ignore[override]
        self._reset()
        done = [g for g in train_games if g.home_score is not None]
        totals = [g.home_score + g.away_score for g in done]  # type: ignore[operator]
        if totals:
            self.league_base = mean(totals)
        for g in sorted(done, key=lambda x: x.kickoff):
            self._observe(g)
        self.sigma_margin = pstdev(self.errors) if len(self.errors) > 30 else 13.5
        self.sigma_total = pstdev(self.t_errors) if len(self.t_errors) > 30 else 10.0

    def observe_game(self, g: GameRow) -> None:
        """Walk-forward driver calls this as results become observable."""
        self._observe(g)

    def predict(self, game: GameRow, features: dict[str, Any] | None, market: MarketRef | None = None):
        self._pre_game(game.home, game.season)
        self._pre_game(game.away, game.season)
        mu_margin = self.r[game.home] - self.r[game.away] + self.hfa
        mu_total = self.s[game.home] + self.s[game.away] + self.league_base
        few = min(self.games_in_season.get(game.home, 0), self.games_in_season.get(game.away, 0))
        widen = 1.0 + (0.15 if few < 3 else 0.0)
        return PredictedMoments(
            mu_margin,
            self.sigma_margin * widen,
            mu_total,
            self.sigma_total * widen,
            {
                "home_rating": self.r[game.home],
                "away_rating": self.r[game.away],
                "hfa": self.hfa,
                "home_scoring": self.s[game.home],
                "away_scoring": self.s[game.away],
            },
        )
