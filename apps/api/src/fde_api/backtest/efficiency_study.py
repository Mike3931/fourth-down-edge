"""Second declared experiment, retaining first experiment's negative result."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from fde_api.backtest.opponent_study import aligned_ratings, cutoff, paired, residual_pairs
from fde_api.backtest.research_comparison import LIMITATIONS, clean, compare_metrics, replay, score_history
from fde_api.backtest.walkforward import precomputed_moments_fn, score_moments
from fde_api.db.models import FeatureSnapshot, ModelEvaluation, Prediction
from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.efficiency_residual import COLUMNS, KEYS, EfficiencyResidual
from fde_api.models_ml.protocol import PredictedMoments
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.registry import register_model
from fde_api.util import current_code_commit

VARIANTS = {
    "epa-primary": (100.0, 365.0),
    "epa-low-alpha": (25.0, 365.0),
    "epa-high-alpha": (400.0, 365.0),
    "epa-fast": (100.0, 180.0),
    "epa-slow": (100.0, 730.0),
}


def lagged_stats(
    games: list[GameRow], stats: list[dict[str, Any]], half_life: float
) -> dict[str, dict[str, Any]]:
    by_id = {g.id: g for g in games}
    indexed: dict[str, list[tuple[datetime, str, str, dict[str, Any]]]] = defaultdict(list)
    if len({(r["game_id"], r["team_id"]) for r in stats}) != len(stats):
        raise ValueError("Duplicate team-game statistic identity")
    for row in stats:
        if row.get("identity_review") or row.get("result_disputed"):
            continue
        game = by_id.get(row["game_id"])
        if game is None or game.result_observed_at is None:
            continue
        if row["team_id"] not in (game.home, game.away):
            raise ValueError("Noncanonical stat-team/game join")
        if game.result_observed_at <= game.kickoff:
            raise ValueError("Invalid result chronology")
        original = datetime.fromisoformat(row["observed_at"])
        original = original.replace(tzinfo=game.kickoff.tzinfo) if original.tzinfo is None else original
        if original <= game.kickoff:
            raise ValueError("Malformed statistic receipt timestamp")
        # Both the stat's recorded receipt and the schedule's result must be visible.
        received = max(original, game.result_observed_at)
        opponent = game.away if row["team_id"] == game.home else game.home
        metrics = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else row["metrics"]
        indexed[row["team_id"]].append((received, game.id, opponent, metrics))

    def rate(team: str, key: str, at: datetime, own: str, defense: bool) -> float | None:
        pool = (
            (r for rows in indexed.values() for r in rows if r[2] == team)
            if defense
            else iter(indexed.get(team, []))
        )
        numerator = denominator = 0.0
        for received, gid, _, metrics in pool:
            value = metrics.get(key)
            if gid == own or received >= at or value is None or not math.isfinite(float(value)):
                continue
            age = (at - received).total_seconds() / 86400
            if age > 5 * 366:
                continue
            weight = 2 ** (-age / half_life)
            numerator += weight * float(value)
            denominator += weight
        return numerator / denominator if denominator else None

    out = {}
    for g in games:
        if g.season < 2019 or g.game_type != "REG":
            continue
        f: dict[str, Any] = {
            "as_of": cutoff(g).isoformat(),
            "publication_semantics": "APPROXIMATED_NOT_ARCHIVED",
        }
        for key in KEYS:
            for defense, label in ((False, "off"), (True, "def")):
                h = rate(g.home, key, cutoff(g), g.id, defense)
                a = rate(g.away, key, cutoff(g), g.id, defense)
                f[f"{label}_diff_{key}"] = h - a if h is not None and a is not None else None
                f[f"{label}_sum_{key}"] = h + a if h is not None and a is not None else None
        out[g.id] = f
    return out


def efficiency_study(
    session: Session,
    history: LeagueHistory,
    market,
    *,
    stats: list[dict[str, Any]],
    source_hash: str,
    stats_hash: str,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    completed = score_history(
        LeagueHistory(
            [
                g
                for g in history.games
                if g.game_type == "REG"
                and g.home_score is not None
                and g.away_score is not None
                and g.season <= 2025
            ],
            [],
        )
    )
    base: dict[str, PredictedMoments] = {}
    for year in range(2019, 2026):
        train = [g for g in completed.games if g.season < year]
        validation = [g for g in completed.games if g.season == year - 1]
        trials = []
        for k in (0.08, 0.12, 0.18):
            m = DynamicRatings(k=k)
            m.fit([g for g in train if g.season < year - 1], {}, completed)
            trials.append(
                (score_moments(precomputed_moments_fn(replay(m, validation)), validation)["log_loss"], k)
            )
        m = DynamicRatings(k=min(trials)[1])
        m.fit(train, {}, completed)
        base.update(aligned_ratings(m, [g for g in completed.games if g.season == year]))
    feature_cache = {}
    all_predictions: dict[str, dict[str, PredictedMoments]] = {"ratings": base}
    folds = []
    rows = []
    for name, (alpha, half_life) in VARIANTS.items():
        if half_life not in feature_cache:
            feature_cache[half_life] = lagged_stats(history.games, stats, half_life)
        features = {
            gid: dict(f)
            | {
                "base_margin": base[gid].mu_margin,
                "base_total": base[gid].mu_total,
                "base_sigma_margin": base[gid].sigma_margin,
                "base_sigma_total": base[gid].sigma_total,
            }
            for gid, f in feature_cache[half_life].items()
            if gid in base
        }
        predictions = {}
        for year in range(2022, 2026):
            train = [g for g in completed.games if 2019 <= g.season < year]
            val = [g for g in train if g.season == year - 1]
            prior = EfficiencyResidual(alpha)
            prior.fit([g for g in train if g.season < year - 1], features, completed)
            validation_moments = {g.id: prior.predict(g, features[g.id]) for g in val}
            residuals = residual_pairs(val, validation_moments)
            model = EfficiencyResidual(alpha)
            model.fit(train, features, completed)
            model.sigma_margin = math.sqrt(sum(x * x for x, _ in residuals) / len(residuals))
            model.sigma_total = math.sqrt(sum(y * y for _, y in residuals) / len(residuals))
            model.scale_provenance = f"prequential corrected residuals from{year - 1}"
            test = [g for g in completed.games if g.season == year]
            scored = {g.id: model.predict(g, features[g.id]) for g in test}
            predictions.update(scored)
            metrics = compare_metrics(scored, test, market)
            folds.append({"model": name, "season": year, "metrics": metrics})
            if name == "epa-primary":
                model.name = f"epa_{run_id}_{year}"
                version = register_model(
                    session,
                    model,
                    target="margin+total research",
                    train_window=f"2019-{year - 1}",
                    validation_window=f"prequential{year - 1}",
                    test_window=str(year),
                    feature_set="epa-residual-v1",
                    calibration_version=None,
                    dataset_hashes={"games": source_hash, "stats": stats_hash},
                    metrics=metrics,
                    known_limitations="; ".join(LIMITATIONS),
                    random_seed=7,
                )
                session.add(
                    ModelEvaluation(
                        id=f"epa-e-{run_id}-{year}",
                        model_version_id=version.id,
                        scope=f"exploratory:{year}",
                        metrics=metrics,
                        sample_size=len(test),
                        created_at=now,
                    )
                )
                for g in test:
                    f = features[g.id]
                    content = hashlib.sha256(json.dumps(f, sort_keys=True).encode()).hexdigest()
                    fid = f"epa-f-{run_id}-{g.id}"
                    session.add(
                        FeatureSnapshot(
                            id=fid,
                            game_id=g.id,
                            feature_set="epa-residual-v1",
                            horizon="RETROSPECTIVE_PREGAME",
                            as_of_at=cutoff(g),
                            values=f,
                            content_hash=content,
                            unavailable_features=dict.fromkeys(
                                ("QB", "personnel", "availability", "weather"),
                                "No archived point-in-time evidence",
                            ),
                            code_commit=current_code_commit(),
                            created_at=now,
                        )
                    )
                    session.flush()
                    session.add(
                        Prediction(
                            id=f"epa-p-{run_id}-{g.id}",
                            game_id=g.id,
                            model_version_id=version.id,
                            feature_snapshot_id=fid,
                            horizon="RETROSPECTIVE_PREGAME",
                            as_of_at=cutoff(g),
                            outputs=scored[g.id].distribution().outputs(None, None)
                            | {"research_only": True, "historical_availability": "APPROXIMATED_NOT_ARCHIVED"},
                            created_at=now,
                        )
                    )
            for g in test:
                rows.append(
                    {
                        "game_id": g.id,
                        "model": name,
                        "season": year,
                        "week": g.week,
                        "as_of": cutoff(g).isoformat(),
                        "home_win_probability": scored[g.id].distribution().home_win_prob(),
                        "margin_mean": scored[g.id].mu_margin,
                        "total_mean": scored[g.id].mu_total,
                        "margin_sigma": scored[g.id].sigma_margin,
                        "total_sigma": scored[g.id].sigma_total,
                        "missing_feature_count": sum(
                            f is None for k, f in features[g.id].items() if k in COLUMNS
                        ),
                    }
                )
        all_predictions[name] = predictions
        print(f"finished efficiency variant{name}", flush=True)
    test = [g for g in completed.games if 2022 <= g.season <= 2025]
    return clean(
        {
            "scope": "EXPLORATORY_NOT_UNTOUCHED",
            "decision": "NO_BET",
            "status": "RESEARCH_ONLY_NO_PROMOTION",
            "run_id": run_id,
            "models": {n: compare_metrics(p, test, market) for n, p in all_predictions.items()},
            "paired": {n: paired(p, base, test) for n, p in all_predictions.items() if n != "ratings"},
            "folds": folds,
            "rows": rows,
            "source_hash": source_hash,
            "stats_hash": stats_hash,
            "n_distinct_games": len(test),
            "independent_prospective_games": 0,
            "limitations": LIMITATIONS,
        }
    )
