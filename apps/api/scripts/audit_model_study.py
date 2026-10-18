"""Read-only artifact replay and missing-feature stress test of a retained study."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fde_api.backtest.research_comparison import clean, compare_metrics
from fde_api.backtest.walkforward import load_market_refs
from fde_api.config import settings
from fde_api.db.models import FeatureSnapshot, ModelVersion, Prediction
from fde_api.features.history import LeagueHistory
from fde_api.models_ml.efficiency_residual import COLUMNS
from fde_api.registry import load_model_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    db = args.database_file.resolve()
    settings.artifacts_dir = db.with_suffix(".research-data") / "artifacts"
    engine = create_engine(f"sqlite:///file:{db.as_posix()}?mode=ro&uri=true")
    cache = {}
    with Session(engine) as session:
        history = LeagueHistory.load(session)
        market, _ = load_market_refs(session)
        predictions = list(
            session.scalars(select(Prediction).where(Prediction.feature_snapshot_id.is_not(None)))
        )
        primary, masked, missing = {}, {}, {}
        missing_count = 0
        for p in predictions:
            mv = session.get(ModelVersion, p.model_version_id)
            assert mv and mv.approval_status == "research_only" and mv.artifact_hash
            if mv.id not in cache:
                path = settings.artifacts_dir / f"model_{mv.name}_{mv.artifact_hash[:12]}.pkl"
                assert hashlib.sha256(path.read_bytes()).hexdigest() == mv.artifact_hash
                cache[mv.id] = load_model_artifact(mv.artifact_hash, mv.name)
            model = cache[mv.id]
            f = session.get(FeatureSnapshot, p.feature_snapshot_id)
            assert f
            assert hashlib.sha256(json.dumps(f.values, sort_keys=True).encode()).hexdigest() == f.content_hash
            game = history.by_id[p.game_id]
            pm = model.predict(game, f.values)
            output = pm.distribution().outputs(None, None)
            assert all(output[k] == p.outputs[k] for k in output), p.id
            primary[game.id] = pm
            if f.feature_set == "epa-residual-v1":
                missing_count += sum(f.values[k] is None for k in COLUMNS)
                masked[game.id] = model.predict(game, f.values | dict.fromkeys(COLUMNS[::2]))
                missing[game.id] = model.predict(game, f.values | dict.fromkeys(COLUMNS))
        games = [history.by_id[gid] for gid in primary]
        actuals = {g.id: (g.home_score - g.away_score, g.home_score + g.away_score) for g in games}  # type: ignore[operator]
        # Independent arithmetic: does not call the repository's loss/MAE helpers.
        win = [
            (pm.distribution().home_win_prob(), int(actuals[gid][0] > 0))
            for gid, pm in primary.items()
            if actuals[gid][0]
        ]
        manual = {
            "n": len(games),
            "n_win_scored": len(win),
            "log_loss": -sum(math.log(p if y else 1 - p) for p, y in win) / len(win),
            "brier": sum((p - y) ** 2 for p, y in win) / len(win),
            "mae_margin": sum(abs(pm.mu_margin - actuals[gid][0]) for gid, pm in primary.items())
            / len(games),
            "mae_total": sum(abs(pm.mu_total - actuals[gid][1]) for gid, pm in primary.items()) / len(games),
        }
        score = compare_metrics(primary, games, market)
        assert all(abs(manual[k] - score[k]) < 1e-10 for k in manual)
        diagnostics = {}
        for name, subset in {
            "early_season": [g for g in games if g.week <= 3],
            "close_final_7_or_less": [g for g in games if abs(actuals[g.id][0]) <= 7],
            "closing_spread_near3": [
                g
                for g in games
                if g.id in market
                and market[g.id].home_line is not None
                and 2.5 <= abs(market[g.id].home_line) <= 3.5
            ],
            "closing_spread_near7": [
                g
                for g in games
                if g.id in market
                and market[g.id].home_line is not None
                and 6.5 <= abs(market[g.id].home_line) <= 7.5
            ],
        }.items():
            diagnostics[name] = compare_metrics(primary, subset, market) if subset else {"n": 0}
        result = {
            "artifacts_hash_verified": len(cache),
            "predictions_exactly_reproduced": len(predictions),
            "feature_hashes_verified": len(predictions),
            "manual_metrics": manual,
            "diagnostics": diagnostics,
            "actual_missing_stat_cells": missing_count,
            "missingness_stress_not_observed_data": True,
            "half_stat_features_suppressed": compare_metrics(masked, games, market) if masked else None,
            "all_stat_features_missing_fallback": compare_metrics(missing, games, market)
            if missing
            else None,
            "decision": "NO_BET",
            "scope": "EXPLORATORY_NOT_UNTOUCHED",
        }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(clean(result), stream, indent=2, allow_nan=False)
    print(
        json.dumps({"replayed": len(predictions), "artifact_count": len(cache), "output": str(args.output)})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
