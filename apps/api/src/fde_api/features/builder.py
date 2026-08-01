"""nfl-core-v1 — governed point-in-time feature set.

Every feature is computed strictly from records with
observed_at <= as_of_at. Features whose honest historical source does
not exist are declared in `unavailable_features` with a reason instead
of being backfilled — DATA INCOMPLETE is a first-class outcome.

Decay & priors
--------------
Team/QB efficiency uses exponential time decay over *prior completed
games*: weight = 0.5 ** (age_days / half_life_days). The half-life is a
tunable selected only inside prior validation windows (see backtest);
the default here is the starting point, not a tuned-on-test value.

At a season boundary a pseudo-observation blends the team's decayed
prior-season value with the league mean (shrinkage), weighted as
`prior_pseudo_games` equivalent games — teams do not reset to identical
confidence, and `*_n_eff` exposes the effective sample so models can
widen early-season uncertainty.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import Any

from sqlalchemy.orm import Session

from fde_api.db.models import FeatureSnapshot
from fde_api.features.geo import TEAM_HOME, travel_miles, tz_change_hours
from fde_api.features.history import GameRow, LeagueHistory, TeamGameRow
from fde_api.pit.clock import PredictionHorizon, horizon_as_of
from fde_api.pit.guards import LookaheadError, assert_no_lookahead
from fde_api.util import current_code_commit, utc_now

FEATURE_SET_VERSION = "nfl-core-v1"

_RATE_KEYS = ("epa_per_play", "epa_per_dropback", "rush_epa_per_play", "success_rate",
              "early_down_epa", "neutral_epa", "explosive_rate", "sack_rate", "int_rate")


@dataclass
class DecayConfig:
    half_life_days: float = 365.0
    prior_pseudo_games: float = 3.0
    prior_league_blend: float = 0.35  # weight on league mean inside the prior
    red_zone_shrink_trips: float = 20.0  # pseudo-trips toward league RZ TD rate


class CoreV1Builder:
    def __init__(self, history: LeagueHistory, decay: DecayConfig | None = None) -> None:
        self.h = history
        self.decay = decay or DecayConfig()

    # ------------------------------------------------------------------ #
    # Decayed team unit strength
    # ------------------------------------------------------------------ #

    def _decayed(self, rows: list[TeamGameRow], key: str, as_of_at: datetime) -> tuple[float | None, float]:
        num = den = 0.0
        for r in rows:
            v = r.stats.get(key)
            if v is None:
                continue
            age_days = (as_of_at - r.kickoff).total_seconds() / 86400.0
            if age_days < 0:
                raise LookaheadError(f"negative age for {r.game_id} at {as_of_at.isoformat()}")
            w = 0.5 ** (age_days / self.decay.half_life_days)
            num += w * v
            den += w
        return (num / den if den > 0 else None), den

    def _league_mean(self, key: str, as_of_at: datetime) -> float | None:
        rows = self.h.all_rows_at(as_of_at)
        vals = [v for r in rows if (v := r.stats.get(key)) is not None]
        return sum(vals) / len(vals) if vals else None

    def _with_season_prior(
        self, team: str, key: str, as_of_at: datetime, season: int, exclude_game_id: str
    ) -> tuple[float | None, float]:
        """Decayed current+prior seasons value with a shrunk season-boundary
        pseudo-observation; returns (value, effective_n)."""
        rows = self.h.rows_for(team, as_of_at, exclude_game_id)
        cur = [r for r in rows if r.season == season]
        value, n_eff = self._decayed(rows, key, as_of_at)
        league = self._league_mean(key, as_of_at)
        if value is None:
            return league, 0.0
        if len(cur) < 6 and league is not None:
            prev = [r for r in rows if r.season < season]
            prev_val, _ = self._decayed(prev, key, as_of_at)
            anchor = league if prev_val is None else (
                self.decay.prior_league_blend * league + (1 - self.decay.prior_league_blend) * prev_val
            )
            k = self.decay.prior_pseudo_games
            cur_val, cur_n = self._decayed(cur, key, as_of_at)
            if cur_val is None:
                return anchor, k
            blended = (cur_n * cur_val + k * anchor) / (cur_n + k)
            return blended, cur_n + k
        return value, n_eff

    def _opponent_adjusted(
        self, team: str, key: str, as_of_at: datetime, season: int, exclude_game_id: str
    ) -> float | None:
        """Per-game opponent adjustment: each observation is corrected by the
        opponent's as-of defensive allowance relative to league mean, then
        decayed. Defensive allowance for X = decayed mean of opponents'
        offense vs X."""
        league = self._league_mean(key, as_of_at)
        if league is None:
            return None
        rows = self.h.rows_for(team, as_of_at, exclude_game_id)
        if not rows:
            return None

        def_allowance: dict[str, float] = {}

        def allowance(opp: str) -> float:
            if opp not in def_allowance:
                faced = [
                    r
                    for r in self.h.all_rows_at(as_of_at)
                    if r.opponent == opp and r.game_id != exclude_game_id
                ]
                v, _ = self._decayed(faced, key, as_of_at)
                def_allowance[opp] = league if v is None else v
            return def_allowance[opp]

        num = den = 0.0
        for r in rows:
            v = r.stats.get(key)
            if v is None:
                continue
            age_days = (as_of_at - r.kickoff).total_seconds() / 86400.0
            w = 0.5 ** (age_days / self.decay.half_life_days)
            num += w * (v - (allowance(r.opponent) - league))
            den += w
        return num / den if den > 0 else None

    def _defense_allowed(self, team: str, key: str, as_of_at: datetime, exclude_game_id: str) -> float | None:
        faced = [r for r in self.h.all_rows_at(as_of_at) if r.opponent == team and r.game_id != exclude_game_id]
        v, _ = self._decayed(faced, key, as_of_at)
        return v

    # ------------------------------------------------------------------ #
    # Composite team features
    # ------------------------------------------------------------------ #

    def _team_features(
        self, team: str, as_of_at: datetime, season: int, exclude_game_id: str, prefix: str
    ) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for key in _RATE_KEYS:
            adj = self._opponent_adjusted(team, key, as_of_at, season, exclude_game_id)
            prior_val, n_eff = self._with_season_prior(team, key, as_of_at, season, exclude_game_id)
            out[f"{prefix}_{key}_adj"] = adj if adj is not None else prior_val
            if key == "epa_per_play":
                out[f"{prefix}_n_eff"] = n_eff
                out[f"{prefix}_uncertainty"] = 1.0 / sqrt(n_eff) if n_eff > 0 else None
        out[f"{prefix}_def_epa_allowed"] = self._defense_allowed(team, "epa_per_play", as_of_at, exclude_game_id)

        rows = self.h.rows_for(team, as_of_at, exclude_game_id)
        pace, _ = self._decayed(rows, "plays", as_of_at)
        drives, _ = self._decayed(rows, "drives", as_of_at)
        out[f"{prefix}_pace_plays"] = pace
        out[f"{prefix}_expected_possessions"] = drives
        st_total, _ = self._decayed(rows, "st_epa_total", as_of_at)
        out[f"{prefix}_special_teams_epa"] = st_total

        # Red-zone TD rate with shrinkage toward league.
        rz_plays = sum(r.stats.get("red_zone_plays") or 0 for r in rows)
        rz_td = sum(r.stats.get("red_zone_td") or 0 for r in rows)
        league_rate = 0.20
        k = self.decay.red_zone_shrink_trips
        out[f"{prefix}_red_zone_td_rate"] = (rz_td + k * league_rate) / (rz_plays + k) if rz_plays >= 0 else None

        # Strength of schedule: mean opponent adjusted offense faced.
        opps = {r.opponent for r in rows if r.season == season}
        if opps:
            vals = []
            for o in opps:
                v, _ = self._with_season_prior(o, "epa_per_play", as_of_at, season, exclude_game_id)
                if v is not None:
                    vals.append(v)
            out[f"{prefix}_sos_epa"] = sum(vals) / len(vals) if vals else None
        else:
            out[f"{prefix}_sos_epa"] = None
        return out

    # ------------------------------------------------------------------ #
    # QB features (team-level proxies; per-player efficiency is v2)
    # ------------------------------------------------------------------ #

    def _qb_features(
        self, team: str, as_of_at: datetime, exclude_game_id: str, prefix: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Expected starter = starter of the team's most recent completed
        game (point-in-time safe). The schedule's own qb column for the
        upcoming game is the *realized* starter and is only used to flag,
        after the fact, how often expectation was wrong — never as input."""
        rows = self.h.rows_for(team, as_of_at, exclude_game_id)
        out: dict[str, Any] = {}
        unavailable: dict[str, str] = {}
        expected_qb: str | None = None
        if rows:
            last = rows[-1]
            g = self.h.by_id[last.game_id]
            expected_qb = g.home_qb_id if last.is_home else g.away_qb_id
        out[f"{prefix}_expected_qb_id"] = expected_qb
        out[f"{prefix}_qb_continuity"] = None if expected_qb is None else 1.0
        # Time-decayed passing efficiency and workload from team dropbacks.
        eff, _ = self._decayed(rows, "epa_per_dropback", as_of_at)
        sack, _ = self._decayed(rows, "sack_rate", as_of_at)
        recent = rows[-4:]
        workload = sum(r.stats.get("dropbacks") or 0 for r in recent) / max(len(recent), 1) if recent else None
        out[f"{prefix}_qb_pass_eff"] = eff
        out[f"{prefix}_qb_sack_avoidance"] = None if sack is None else -sack
        out[f"{prefix}_qb_recent_workload"] = workload
        unavailable[f"{prefix}_qb_rushing_contribution"] = (
            "per-QB rushing split requires passer-level aggregates (planned v2)"
        )
        unavailable[f"{prefix}_qb_experience"] = "roster join to starter pending per-QB table (planned v2)"
        unavailable[f"{prefix}_backup_replacement_delta"] = (
            "no defensible historical backup-quality source; placeholder by design"
        )
        return out, unavailable

    # ------------------------------------------------------------------ #
    # Snapshot assembly
    # ------------------------------------------------------------------ #

    def build(self, game: GameRow, horizon: PredictionHorizon, session: Session | None = None) -> FeatureSnapshot:
        as_of_at = horizon_as_of(game.kickoff, horizon)
        # A snapshot may never be built after the game it predicts has begun
        # (except the evaluation-only closing capture at kickoff itself).
        if horizon is not PredictionHorizon.CLOSING_CAPTURE:
            assert_no_lookahead(as_of_at, game.kickoff, "snapshot cutoff")
        if game.result_observed_at is not None and game.result_observed_at <= as_of_at:
            raise LookaheadError(f"game {game.id} already has an observed result at {as_of_at.isoformat()}")

        values: dict[str, Any] = {
            "season": game.season,
            "week": game.week,
            "game_type": game.game_type,
            "roof": game.roof,
            "surface": game.surface,
            "is_indoor": game.roof in ("dome", "closed") if game.roof else None,
            "div_game": game.div_game,
            "home_rest_days": game.home_rest,
            "away_rest_days": game.away_rest,
            "home_short_week": None if game.home_rest is None else game.home_rest < 6,
            "away_short_week": None if game.away_rest is None else game.away_rest < 6,
            "home_off_bye": None if game.home_rest is None else game.home_rest >= 13,
            "away_off_bye": None if game.away_rest is None else game.away_rest >= 13,
            "away_travel_miles": travel_miles(game.away, game.home),
            "away_tz_change_hours": tz_change_hours(game.away, game.home),
            "home_field": True,
            "neutral_site_uncertain": game.stadium_id is not None and game.home not in TEAM_HOME,
        }
        unavailable: dict[str, str] = {}

        values |= self._team_features(game.home, as_of_at, game.season, game.id, "home")
        values |= self._team_features(game.away, as_of_at, game.season, game.id, "away")

        qb_h, un_h = self._qb_features(game.home, as_of_at, game.id, "home")
        qb_a, un_a = self._qb_features(game.away, as_of_at, game.id, "away")
        values |= qb_h | qb_a
        unavailable |= un_h | un_a

        # Honest unavailability declarations for this phase.
        for name, reason in _STRUCTURALLY_UNAVAILABLE.items():
            unavailable[name] = reason

        payload = json.dumps({"values": values, "unavailable": unavailable}, sort_keys=True, default=str)
        content_hash = hashlib.sha256(payload.encode()).hexdigest()
        snap = FeatureSnapshot(
            id=f"fs_{game.id}_{horizon.value}_{content_hash[:12]}",
            game_id=game.id,
            feature_set=FEATURE_SET_VERSION,
            horizon=horizon.value,
            as_of_at=as_of_at,
            values=values,
            unavailable_features=unavailable,
            content_hash=content_hash,
            code_commit=current_code_commit(),
            created_at=utc_now(),
        )
        if session is not None:
            session.merge(snap)
        return snap


_STRUCTURALLY_UNAVAILABLE: dict[str, str] = {
    "market_consensus_spread": "no licensed historical multi-book odds; nflverse closing lines are evaluation-only",
    "market_consensus_total": "no licensed historical multi-book odds; nflverse closing lines are evaluation-only",
    "market_no_vig_win_prob": "requires pre-close consensus quotes (adapter contract exists, no feed)",
    "market_line_movement": "requires opening + intraweek line history (no source)",
    "market_spread_dispersion": "requires multi-book quotes (no source)",
    "market_price_dispersion": "requires multi-book quotes (no source)",
    "market_quote_age": "requires timestamped quotes (manual-entry or feed only, forward capture)",
    "market_eligible_books": "requires multi-book quotes (no source)",
    "player_active_probability": "no reliable point-in-time historical injury feed; forward capture only",
    "player_expected_snap_share": "participation backfill pending; would be retrospective otherwise",
    "player_restriction_probability": "no point-in-time injury designations historically",
    "player_replacement_quality": "requires per-player valuation model (later phase)",
    "position_group_continuity": "requires weekly depth charts with observation instants",
    "weather_temp_forecast": "NWS serves forward forecasts only; historical pregame forecasts unavailable",
    "weather_wind_forecast": "NWS serves forward forecasts only",
    "weather_gust_forecast": "NWS serves forward forecasts only",
    "weather_precip_prob": "NWS serves forward forecasts only",
    "referee_crew_effect": "insufficient per-crew sample for unshrunk effects; excluded from v1 by policy",
}
