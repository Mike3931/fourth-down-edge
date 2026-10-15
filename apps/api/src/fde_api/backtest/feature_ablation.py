"""Exploratory chronological rest-feature ablation, never a promotion decision.

Uses existing ridge/calibration/scoring/registry machinery. Previously inspected
test seasons are explicitly reused diagnostics, not unseen confirmation data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from fde_api.backtest.research_comparison import clean, compare_metrics, score_history
from fde_api.calibration import fit_calibration
from fde_api.db.models import BacktestRun, ModelEvaluation
from fde_api.features.builder import CoreV1Builder
from fde_api.features.history import LeagueHistory
from fde_api.models_ml.glm import FEATURE_COLUMNS, RidgeBaseline
from fde_api.pit.clock import PredictionHorizon
from fde_api.registry import register_model


def run_ablation(s: Session, *, seasons: list[int], source_hash: str) -> dict[str, Any]:
    history = score_history(LeagueHistory.load(s))
    builder = CoreV1Builder(history)
    raw = {
        g.id: builder.build(g, PredictionHorizon.PREGAME).values
        for g in history.games
        if g.home_score is not None and g.away_score is not None
    }
    run_id = "abl_" + uuid.uuid4().hex[:12]
    result: dict[str, Any] = {
        "run_id": run_id,
        "scope": "EXPLORATORY_REUSED_SEASONS",
        "source_hash": source_hash,
        "created_at": datetime.now(UTC).isoformat(),
        "folds": [],
        "limitations": [
            "Previously examined test seasons are not unseen confirmation.",
            "Mutable historical rest metadata and approximate result times are not archived PIT evidence.",
            "Efficiency, pace, QB, availability and weather effects lack accepted multi-season feature vintages here.",
            "All unsuccessful trials retained. No prospective candidate or model is promoted.",
        ],
        "feature_columns": list(FEATURE_COLUMNS),
        "recommended_for_review": [],
    }
    for season in seasons:
        train = [g for g in history.games if g.season <= season - 2 and g.id in raw]
        val = [g for g in history.games if g.season == season - 1 and g.id in raw and g.game_type == "REG"]
        test = [g for g in history.games if g.season == season and g.id in raw and g.game_type == "REG"]
        if not train or not val or not test:
            raise ValueError("Missing chronological ablation fold")
        for columns in [(), ("home_rest_days", "away_rest_days")]:
            # Zeroed columns are explicit removed regressors, not invented measurements.
            features = {
                gid: {c: values.get(c) if c in columns else 0.0 for c in FEATURE_COLUMNS}
                for gid, values in raw.items()
            }
            trials = []
            for alpha in [25.0, 100.0]:
                model = RidgeBaseline(alpha=alpha)
                model.fit(train, features, history)
                moments = {g.id: model.predict(g, features[g.id], None) for g in val}
                metrics = compare_metrics(moments, val, {})
                trials.append({"alpha": alpha, "validation_log_loss": metrics["log_loss"]})
            alpha = min(trials, key=lambda t: t["validation_log_loss"])["alpha"]
            model = RidgeBaseline(alpha=alpha)
            model.fit(train, features, history)
            # Calibration uses only the prior validation season, never the test fold.
            binary = [g for g in val if g.home_score != g.away_score]
            probs = [model.predict(g, features[g.id], None).distribution().home_win_prob() for g in binary]
            ys = [int(g.home_score > g.away_score) for g in binary]  # type: ignore[operator]
            calibrator = fit_calibration("platt", probs, ys)
            model.fit(train + val, features, history)
            label = "rest" if columns else "intercept"
            model.name = f"{run_id}_{season}_{label}"
            mv = register_model(
                s,
                model,
                target="rest ablation research",
                train_window=f"through {season - 2}",
                validation_window=str(season - 1),
                test_window=str(season),
                feature_set="nfl-core-v1",
                calibration_version=None,
                dataset_hashes={"games": source_hash},
                metrics={},
                known_limitations="; ".join(result["limitations"]),
                random_seed=20260801,
            )
            moments = {g.id: model.predict(g, features[g.id], None) for g in test}
            metrics = compare_metrics(moments, test, {})
            test_binary = [g for g in test if g.home_score != g.away_score]
            from fde_api.evaluation import brier, log_loss

            calibrated = [calibrator.apply(moments[g.id].distribution().home_win_prob()) for g in test_binary]
            actual = [int(g.home_score > g.away_score) for g in test_binary]  # type: ignore[operator]
            calibrated_metrics = {
                "brier": brier(calibrated, actual),
                "log_loss": log_loss(calibrated, actual),
            }
            fold = {
                "test_season": season,
                "features": list(columns),
                "train_n": len(train),
                "validation_n": len(val),
                "trials": trials,
                "selected_alpha": alpha,
                "metrics": metrics,
                "calibration_parameters": calibrator.parameters,
                "calibrated_moneyline": calibrated_metrics,
                "model_version_id": mv.id,
                "status": "RESEARCH ONLY",
            }
            s.add(
                ModelEvaluation(
                    id=f"ev_{mv.id}",
                    model_version_id=mv.id,
                    scope=f"exploratory:{season}:{label}",
                    metrics=clean(fold),
                    sample_size=len(test),
                    created_at=datetime.now(UTC),
                )
            )
            result["folds"].append(fold)
    s.add(
        BacktestRun(
            id=run_id,
            config={"version": "rest-ablation-v1", "seasons": seasons},
            status="finished",
            metrics=clean(result),
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
    )
    s.flush()
    return clean(result)
