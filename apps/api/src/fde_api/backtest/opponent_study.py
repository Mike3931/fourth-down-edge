"""Chronological, common-game exploratory study. No live forecast integration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from fde_api.backtest.research_comparison import (
    LIMITATIONS,
    clean,
    compare_metrics,
    loss,
    market_probability,
    replay,
    score_history,
)
from fde_api.backtest.walkforward import precomputed_moments_fn, score_moments
from fde_api.db.models import FeatureSnapshot, ModelEvaluation, Prediction
from fde_api.evaluation import week_block_bootstrap
from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.baselines import HomeFieldBaseline, RollingAverageBaseline
from fde_api.models_ml.opponent_scores import MISSING_INPUTS, OpponentScores
from fde_api.models_ml.protocol import MarketRef, PredictedMoments
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.registry import register_model
from fde_api.util import current_code_commit

CONFIGS = {
    "primary": (25.0, 365.0),
    "short-memory": (25.0, 180.0),
    "long-memory": (25.0, 730.0),
    "weak-shrinkage": (10.0, 365.0),
    "strong-shrinkage": (100.0, 365.0),
}


def residual_pairs(games: list[GameRow], moments: dict[str, PredictedMoments]) -> list[tuple[float, float]]:
    result = []
    for g in games:
        assert g.home_score is not None and g.away_score is not None
        result.append(
            (
                g.home_score - g.away_score - moments[g.id].mu_margin,
                g.home_score + g.away_score - moments[g.id].mu_total,
            )
        )
    return result


def cutoff(game: GameRow) -> datetime:
    return game.kickoff - timedelta(minutes=90)


def raw_season(
    history: LeagueHistory, season: int, alpha: float, half_life: float
) -> tuple[dict[str, PredictedMoments], dict[datetime, OpponentScores]]:
    out, models = {}, {}
    for g in history.games:
        if g.season != season or g.game_type != "REG":
            continue
        at = cutoff(g)
        if at not in models:
            m = OpponentScores(alpha, half_life)
            m.fit_at(history.games, at)
            models[at] = m
        out[g.id] = models[at].predict(g)
    return out, models


def aligned_ratings(model: DynamicRatings, games: list[GameRow]) -> dict[str, PredictedMoments]:
    observed: set[str] = set()
    observable = sorted(games, key=lambda g: (g.result_observed_at, g.id))
    out = {}
    for g in sorted(games, key=lambda g: (g.kickoff, g.id)):
        for old in observable:
            if (
                old.id != g.id
                and old.id not in observed
                and old.result_observed_at
                and old.result_observed_at < cutoff(g)
            ):
                model.observe_game(old)
                observed.add(old.id)
        out[g.id] = model.predict(g, None)
    return out


def paired(
    a: dict[str, PredictedMoments], b: dict[str, PredictedMoments], games: list[GameRow]
) -> dict[str, Any]:
    values: dict[str, list[float]] = {k: [] for k in ("log_loss", "brier", "margin_mae", "total_mae")}
    blocks: dict[str, list[str]] = {k: [] for k in values}
    for g in games:
        x, z = a[g.id], b[g.id]
        assert g.home_score is not None and g.away_score is not None
        margin, total = g.home_score - g.away_score, g.home_score + g.away_score
        deltas = {
            "margin_mae": abs(x.mu_margin - margin) - abs(z.mu_margin - margin),
            "total_mae": abs(x.mu_total - total) - abs(z.mu_total - total),
        }
        if margin:
            y = int(margin > 0)
            p, q = x.distribution().home_win_prob(), z.distribution().home_win_prob()
            deltas.update(log_loss=loss(p, y) - loss(q, y), brier=(p - y) ** 2 - (q - y) ** 2)
        for key, delta in deltas.items():
            values[key].append(delta)
            blocks[key].append(f"{g.season}-{g.week}")
    return {k: asdict(week_block_bootstrap(v, blocks[k])) for k, v in values.items() if v}


def study(
    session: Session,
    history: LeagueHistory,
    market: dict[str, MarketRef],
    *,
    seasons: list[int],
    source_hash: str,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    if seasons != sorted(set(seasons)) or not seasons or min(seasons) < 2012 or max(seasons) > 2025:
        raise ValueError("Distinct ascending exploratory seasons through2025 only")
    # Refuse malformed result chronology even outside a currently scored fold.
    if any(g.result_observed_at and g.result_observed_at <= g.kickoff for g in history.games):
        raise ValueError("Invalid historical result chronology")
    history = score_history(
        LeagueHistory(
            [
                g
                for g in history.games
                if g.season <= 2025
                and g.game_type == "REG"
                and g.home_score is not None
                and g.away_score is not None
            ],
            [],
        )
    )
    names = ["naive-homefield-v1", "naive-rolling-v1", "team-ratings-v1", *CONFIGS]
    all_predictions: dict[str, dict[str, PredictedMoments]] = {n: {} for n in names}
    folds, rows = [], []
    cache: dict[tuple[str, int], tuple[dict[str, PredictedMoments], dict[datetime, OpponentScores]]] = {}
    for year in seasons:
        train = [g for g in history.games if g.season < year]
        test = [g for g in history.games if g.season == year]
        validation = [g for g in history.games if g.season == year - 1]
        if not train or not test or not validation:
            raise ValueError("Missing train/validation/test games")
        fold: dict[str, Any] = {"season": year, "n": len(test), "models": {}}
        # Preserve the existing, prior-season-only k selection rule.
        trials = []
        for k in (0.08, 0.12, 0.18):
            m = DynamicRatings(k=k)
            m.fit([g for g in train if g.season < year - 1], {}, history)
            trials.append(
                (score_moments(precomputed_moments_fn(replay(m, validation)), validation)["log_loss"], k)
            )
        fold["prior_season_k_trials"] = trials
        for model in (HomeFieldBaseline(), RollingAverageBaseline(), DynamicRatings(k=min(trials)[1])):
            model.fit(train, {}, history)
            moments = (
                aligned_ratings(model, test) if isinstance(model, DynamicRatings) else replay(model, test)
            )
            all_predictions[model.name].update(moments)
            fold["models"][model.name] = compare_metrics(moments, test, market)
        for name, (alpha, half_life) in CONFIGS.items():
            for season in (year - 1, year):
                if (name, season) not in cache:
                    cache[name, season] = raw_season(history, season, alpha, half_life)
            previous, _ = cache[name, year - 1]
            residuals = residual_pairs(validation, previous)
            moments, fitted = cache[name, year]
            calibrator = OpponentScores(alpha, half_life)
            calibrator.calibrate_scale(
                residuals, provenance=f"prequential residuals from{year - 1}; no test calibration"
            )
            scaled = {
                gid: replace(pm, sigma_margin=calibrator.sigma_margin, sigma_total=calibrator.sigma_total)
                for gid, pm in moments.items()
            }
            all_predictions[name].update(scaled)
            fold["models"][name] = compare_metrics(scaled, test, market)
            if name == "primary":
                versions = {}
                for index, (at, fitted_model) in enumerate(sorted(fitted.items())):
                    fitted_model.sigma_margin, fitted_model.sigma_total = (
                        calibrator.sigma_margin,
                        calibrator.sigma_total,
                    )
                    fitted_model.scale_provenance = calibrator.scale_provenance
                    fitted_model.name = f"od_{run_id}_{year}_{index}"
                    version = register_model(
                        session,
                        fitted_model,
                        target="margin+total research",
                        train_window=f"strictly before{at.date()}",
                        validation_window=str(year - 1),
                        test_window=str(year),
                        feature_set="opponent-scores-v1",
                        calibration_version=None,
                        dataset_hashes={"licensed_games_sha256": source_hash},
                        metrics={},
                        known_limitations="; ".join(LIMITATIONS),
                        random_seed=7,
                    )
                    versions[at] = version.id
                session.flush()
                for g in test:
                    at = cutoff(g)
                    payload = {
                        "home": g.home,
                        "away": g.away,
                        "kickoff": g.kickoff.isoformat(),
                        "as_of": at.isoformat(),
                        "source_hash": source_hash,
                        "model_version": versions[at],
                        "scope": "RETROSPECTIVE_APPROXIMATED_AVAILABILITY",
                        "missing_inputs": list(MISSING_INPUTS),
                    }
                    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    fid = f"od-f-{run_id}-{g.id}"
                    session.add(
                        FeatureSnapshot(
                            id=fid,
                            game_id=g.id,
                            feature_set="opponent-scores-v1",
                            horizon="RETROSPECTIVE_PREGAME",
                            as_of_at=at,
                            values=payload,
                            unavailable_features=dict.fromkeys(
                                MISSING_INPUTS, "Not supported by archived point-in-time evidence"
                            ),
                            content_hash=digest,
                            code_commit=current_code_commit(),
                            created_at=now,
                        )
                    )
                    session.flush()
                    output = scaled[g.id].distribution().outputs(None, None) | {
                        "research_only": True,
                        "historical_availability": "APPROXIMATED_NOT_ARCHIVED",
                        "missing_inputs": list(MISSING_INPUTS),
                    }
                    session.add(
                        Prediction(
                            id=f"od-p-{run_id}-{g.id}",
                            game_id=g.id,
                            model_version_id=versions[at],
                            feature_snapshot_id=fid,
                            horizon="RETROSPECTIVE_PREGAME",
                            as_of_at=at,
                            outputs=output,
                            created_at=now,
                        )
                    )
                session.add(
                    ModelEvaluation(
                        id=f"od-e-{run_id}-{year}",
                        model_version_id=next(iter(versions.values())),
                        scope=f"exploratory fold:{year}",
                        metrics=fold["models"][name]
                        | {
                            "evaluation_grain": "aggregate fold across member versions",
                            "member_model_versions": list(versions.values()),
                        },
                        sample_size=len(test),
                        created_at=now,
                    )
                )
        folds.append(fold)
        print(f"finished exploratory season{year}", flush=True)
    games = [g for g in history.games if g.season in seasons]
    aggregate = {n: compare_metrics(p, games, market) for n, p in all_predictions.items()}
    for g in games:
        assert g.home_score is not None and g.away_score is not None
        for name, predictions in all_predictions.items():
            pm = predictions[g.id]
            rows.append(
                {
                    "game_id": g.id,
                    "season": g.season,
                    "week": g.week,
                    "model": name,
                    "as_of": cutoff(g).isoformat(),
                    "home_win_probability": pm.distribution().home_win_prob(),
                    "away_win_probability": 1 - pm.distribution().home_win_prob(),
                    "margin_mean": pm.mu_margin,
                    "total_mean": pm.mu_total,
                    "margin_sigma": pm.sigma_margin,
                    "total_sigma": pm.sigma_total,
                    "actual_margin": g.home_score - g.away_score,
                    "actual_total": g.home_score + g.away_score,
                }
            )
    diagnostics: dict[str, Any] = {}
    primary = all_predictions["primary"]
    for label, subset in {
        "early_season": [g for g in games if g.week <= 3],
        "close_final": [g for g in games if abs(g.home_score - g.away_score) <= 7],  # type: ignore[operator]
        "strong_market_favorite": [
            g
            for g in games
            if (probability := market_probability(market.get(g.id))) is not None
            and max(probability, 1 - probability) >= 0.7
        ],  # type: ignore[arg-type,operator]
    }.items():
        diagnostics[label] = (
            {
                n: compare_metrics(p, subset, market)
                for n, p in (("primary", primary), ("ratings", all_predictions["team-ratings-v1"]))
            }
            if subset
            else {"n": 0}
        )
    # Withhold all results an additional day; all model selection stays frozen.
    delayed = score_history(
        LeagueHistory(
            [replace(g, result_observed_at=g.kickoff + timedelta(hours=24)) for g in history.games], []
        )
    )
    delayed_predictions = {}
    for year in seasons:
        previous, _ = raw_season(delayed, year - 1, 25.0, 365.0)
        residuals = residual_pairs([g for g in delayed.games if g.season == year - 1], previous)
        delayed_calibrator = OpponentScores()
        delayed_calibrator.calibrate_scale(residuals, provenance="delayed prior-season residuals")
        raw, _ = raw_season(delayed, year, 25.0, 365.0)
        delayed_predictions.update(
            {
                gid: replace(
                    pm,
                    sigma_margin=delayed_calibrator.sigma_margin,
                    sigma_total=delayed_calibrator.sigma_total,
                )
                for gid, pm in raw.items()
            }
        )
    return clean(
        {
            "scope": "EXPLORATORY_NOT_UNTOUCHED",
            "run_id": run_id,
            "cutoff": "kickoff minus90minutes; results strictly before cutoff",
            "limitations": LIMITATIONS,
            "models": aggregate,
            "folds": folds,
            "paired_primary_minus_ratings": paired(primary, all_predictions["team-ratings-v1"], games),
            "paired_sensitivities_minus_ratings": {
                n: paired(all_predictions[n], all_predictions["team-ratings-v1"], games) for n in CONFIGS
            },
            "delay24h_primary": compare_metrics(delayed_predictions, games, market),
            "diagnostics": diagnostics,
            "rows": rows,
            "source_hash": source_hash,
            "n_distinct_games": len(games),
            "independent_prospective_games": 0,
            "status": "RESEARCH_ONLY_NO_PROMOTION",
            "decision": "NO_BET",
        }
    )
