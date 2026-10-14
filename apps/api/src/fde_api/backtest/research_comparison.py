"""Research comparison built on existing predictors, replay, metrics and registry.

No closing prices enter any candidate. Public mutable historical results support
retrospective diagnostics, NOT proof of historical point-in-time availability.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.backtest.walkforward import (
    load_market_refs,
    precomputed_moments_fn,
    score_moments,
    sequential_ratings_moments,
)
from fde_api.calibration import FittedCalibration
from fde_api.db.models import BacktestRun, Game, ModelEvaluation, Prediction
from fde_api.evaluation import brier, log_loss, reliability_bins, week_block_bootstrap
from fde_api.features.history import GameRow, LeagueHistory, TeamGameRow
from fde_api.models_ml.baselines import HomeFieldBaseline, RollingAverageBaseline
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.registry import register_model

VERSION = "singles-comparison-v1"
LIMITATIONS = [
    "RESEARCH ONLY: no model is approved for recommendations or wagering.",
    "Retrospective mutable public data; result receipt times are approximated, not archived historical vintages.",
    "2024 and 2025 were previously studied. These are out-of-season folds, not untouched confirmation data.",
    "Closing prices are evaluation-only benchmarks, never candidate inputs or verified bet365 offers.",
    "Score-only models omit official availability, quarterback changes, routes and matchup personnel.",
    "Normal margin/total distributions approximate key-number tails; no SGP joint-probability claim.",
]


def clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    return value


def score_history(history: LeagueHistory) -> LeagueHistory:
    """Only score-derived rows; no invented EPA or retrospective roster features."""
    rows = []
    for g in history.games:
        if g.home_score is None or g.away_score is None or g.result_observed_at is None:
            continue
        for team, opponent, home in ((g.home, g.away, True), (g.away, g.home, False)):
            rows.append(TeamGameRow(g.id, g.season, g.week, team, opponent, home,
                                   g.kickoff, g.result_observed_at, {}))
    # The existing fitter uses a stable kickoff sort. Supply a total order so
    # simultaneous training updates never depend on database row order.
    return LeagueHistory(sorted(history.games, key=lambda g: (g.kickoff, g.id)), rows)


def replay(model: GamePredictor, games: list[GameRow]) -> dict[str, PredictedMoments]:
    if isinstance(model, DynamicRatings):
        return sequential_ratings_moments(model, games)
    return {g.id: model.predict(g, None, None) for g in games}


def loss(p: float, y: int) -> float:
    return -math.log(max(1e-9, min(1 - 1e-9, p if y else 1 - p)))


def market_probability(ref: MarketRef | None) -> float | None:
    if ref is None or ref.home_ml is None or ref.away_ml is None:
        return None
    def implied(price: int) -> float:
        return abs(price) / (abs(price) + 100) if price < 0 else 100 / (price + 100)
    home, away = implied(ref.home_ml), implied(ref.away_ml)
    return home / (home + away)


def compare_metrics(moments: dict[str, PredictedMoments], games: list[GameRow],
                    market: dict[str, MarketRef]) -> dict[str, Any]:
    metrics = score_moments(precomputed_moments_fn(moments), games, FittedCalibration("none", {}, 0))
    metrics["mae_margin"] = metrics.pop("margin_mae", None)
    metrics["mae_total"] = metrics.pop("total_mae", None)
    probabilities: dict[str, list[float]] = {"spread": [], "total": []}
    outcomes: dict[str, list[int]] = {"spread": [], "total": []}
    pushes = {"spread": 0, "total": 0}
    covered = {"margin": 0, "total": 0}
    market_p, candidate_p, ys, blocks = [], [], [], []
    market_margin_errors, model_margin_errors, market_total_errors, model_total_errors = [], [], [], []
    for g in games:
        pm = moments[g.id]
        assert g.home_score is not None and g.away_score is not None
        margin, total = g.home_score - g.away_score, g.home_score + g.away_score
        covered["margin"] += abs(margin - pm.mu_margin) <= 1.2815515655 * pm.sigma_margin
        covered["total"] += abs(total - pm.mu_total) <= 1.2815515655 * pm.sigma_total
        ref = market.get(g.id)
        if ref is None:
            continue
        mp = market_probability(ref)
        if mp is not None and margin != 0:
            market_p.append(mp)
            candidate_p.append(pm.distribution().home_win_prob())
            ys.append(int(margin > 0))
            blocks.append(f"{g.season}-{g.week}")
        for category, line, actual, probs in (
            ("spread", ref.home_line, margin, pm.distribution().spread_probs),
            ("total", ref.total_line, total, pm.distribution().total_probs),
        ):
            if line is None:
                continue
            result = actual + line if category == "spread" else actual - line
            if abs(result) < 1e-9:
                pushes[category] += 1
                continue
            win, _push, lose = probs(line)
            probabilities[category].append(win / (win + lose))
            outcomes[category].append(int(result > 0))
        if ref.home_line is not None:
            market_margin_errors.append(abs(margin + ref.home_line))
            model_margin_errors.append(abs(margin - pm.mu_margin))
        if ref.total_line is not None:
            market_total_errors.append(abs(total - ref.total_line))
            model_total_errors.append(abs(total - pm.mu_total))
    for category in probabilities:
        p, y = probabilities[category], outcomes[category]
        metrics[category] = {"n": len(y), "pushes_excluded": pushes[category],
            "brier": brier(p, y) if y else None, "log_loss": log_loss(p, y) if y else None,
            "reliability": reliability_bins(p, y) if y else [],
            "benchmark": "0.5 conditional on no push; closing line used for evaluation only"}
    deltas = [loss(p, y) - loss(mp, y) for p, mp, y in zip(candidate_p, market_p, ys, strict=True)]
    interval = week_block_bootstrap(deltas, blocks) if deltas else None
    def avg(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None
    metrics["closing_benchmark"] = {"n": len(ys), "log_loss": log_loss(market_p, ys) if ys else None,
        "brier": brier(market_p, ys) if ys else None,
        "candidate_log_loss_same_sample": log_loss(candidate_p, ys) if ys else None,
        "paired_log_loss_delta_ci90": [interval.lo, interval.hi] if interval else None,
        "margin_n": len(market_margin_errors), "margin_mae": avg(market_margin_errors),
        "candidate_margin_mae_same_sample": avg(model_margin_errors),
        "total_n": len(market_total_errors), "total_mae": avg(market_total_errors),
        "candidate_total_mae_same_sample": avg(model_total_errors)}
    metrics["interval_80_coverage"] = {k: v / len(games) for k, v in covered.items()}
    return clean(metrics)


def validation_status(metrics: dict[str, Any], baseline: dict[str, Any]) -> tuple[str, list[str]]:
    required = ("log_loss", "brier", "mae_margin", "mae_total")
    if (metrics.get("n", 0) < 500 or metrics.get("closing_benchmark", {}).get("n", 0) < 500
            or any(metrics.get(k) is None or baseline.get(k) is None for k in required)):
        return "INSUFFICIENT EVIDENCE", ["Fewer than 500 common scored games or closing benchmarks"]
    failures = []
    if metrics["log_loss"] >= baseline["log_loss"]:
        failures.append("Moneyline log loss does not beat home-field baseline")
    if metrics["brier"] >= baseline["brier"]:
        failures.append("Moneyline Brier score does not beat home-field baseline")
    if metrics["mae_margin"] >= baseline["mae_margin"]:
        failures.append("Margin MAE does not beat home-field baseline")
    if metrics["mae_total"] >= baseline["mae_total"]:
        failures.append("Total MAE does not beat home-field baseline")
    if any(not 0.72 <= v <= 0.88 for v in metrics["interval_80_coverage"].values()):
        failures.append("80% predictive interval coverage outside declared 72–88% tolerance")
    benchmark = metrics["closing_benchmark"]
    if not benchmark.get("paired_log_loss_delta_ci90") or benchmark["paired_log_loss_delta_ci90"][1] >= 0:
        failures.append("No paired evidence of lower moneyline log loss than closing-market benchmark")
    if benchmark.get("margin_mae") is None or benchmark.get("candidate_margin_mae_same_sample", math.inf) >= benchmark["margin_mae"]:
        failures.append("Margin MAE does not beat closing-line benchmark on the same games")
    if benchmark.get("total_mae") is None or benchmark.get("candidate_total_mae_same_sample", math.inf) >= benchmark["total_mae"]:
        failures.append("Total MAE does not beat closing-line benchmark on the same games")
    if failures:
        return "VALIDATION FAILED", failures
    return "INSUFFICIENT EVIDENCE", [
        "Retrospective diagnostic gates passed; archived vintages and independent prospective confirmation required",
        "No production promotion or verified bet365 value assessment"]


def run_comparison(session: Session, *, test_seasons: list[int], source_hash: str,
                   now: datetime) -> dict[str, Any]:
    history = score_history(LeagueHistory.load(session))
    market, _ = load_market_refs(session)
    completed = [g for g in history.games if g.game_type == "REG" and g.home_score is not None
                 and g.away_score is not None and g.result_observed_at and g.result_observed_at < now]
    if sorted(set(test_seasons)) != test_seasons or not test_seasons:
        raise ValueError("Distinct ascending test seasons required")
    run_id = "cmp_" + uuid.uuid4().hex[:12]
    run = BacktestRun(id=run_id, config={"comparison_version": VERSION, "test_seasons": test_seasons,
        "source_hash": source_hash, "ratings_k_grid": [0.08, 0.12, 0.18],
        "scope": "RETROSPECTIVE_KICKOFF", "calibration": "identity; reliability assessed without tuning",
        "validation_thresholds": {"min_n": 500, "interval80_tolerance": [0.72, 0.88],
            "market_gate": "paired ML loss delta upper90 < 0, margin/total MAE below closing lines",
            "prospective_confirmation": "required; retrospective results cannot qualify"}},
        status="running", created_at=now)
    session.add(run)
    session.flush()
    all_moments: dict[str, dict[str, PredictedMoments]] = {}
    folds, stored, test_all = [], [], []
    for season in test_seasons:
        train = [g for g in completed if g.season <= season - 2]
        val = [g for g in completed if g.season == season - 1]
        test = [g for g in completed if g.season == season]
        if not train or not val or not test:
            raise ValueError(f"Missing training/validation/test season for {season}")
        if max(g.result_observed_at for g in train + val if g.result_observed_at) >= min(g.kickoff for g in test):
            raise ValueError("Training results overlap test cutoff")
        sensitivity = []
        for k in (0.08, 0.12, 0.18):
            candidate = DynamicRatings(k=k)
            candidate.fit(train, {}, history)
            scored = score_moments(precomputed_moments_fn(replay(candidate, val)), val)
            sensitivity.append({"k": k, "validation_log_loss": scored["log_loss"],
                                "validation_margin_mae": scored["margin_mae"]})
        best_k = min(sensitivity, key=lambda v: v["validation_log_loss"])["k"]
        models = [HomeFieldBaseline(), RollingAverageBaseline(), DynamicRatings(k=best_k)]
        fold: dict[str, Any] = {"test_season": season, "train_through": season - 2,
            "validation_season": season - 1, "train_n": len(train), "validation_n": len(val),
            "test_n": len(test), "sensitivity": sensitivity, "selected_k": best_k, "models": {}}
        for model in models:
            name = model.name
            model.fit(train + val, {}, history)
            # Unique existing registry IDs preserve every earlier artifact/evaluation.
            model.name = f"{name}_{run_id}_{season}"
            registered = register_model(session, model, target="margin+total research",
                train_window=f"through {season-2}", validation_window=str(season-1), test_window=str(season),
                feature_set="scores-only-v1", calibration_version=None, dataset_hashes={"games_sha256": source_hash},
                metrics={}, known_limitations="; ".join(LIMITATIONS), random_seed=20260801)
            moments = replay(model, test)
            all_moments.setdefault(name, {}).update(moments)
            metrics = compare_metrics(moments, test, market)
            fold["models"][name] = metrics
            session.add(ModelEvaluation(id=f"ev_{registered.id}", model_version_id=registered.id,
                scope=f"retrospective:{season}", metrics=metrics, sample_size=len(test), created_at=now))
            for g in test:
                session.add(Prediction(id=f"{registered.id}_{g.id}", game_id=g.id,
                    model_version_id=registered.id, feature_snapshot_id=None, horizon="RETROSPECTIVE_KICKOFF",
                    as_of_at=g.kickoff, outputs=clean(moments[g.id].distribution().outputs(None, None)) |
                    {"research_only": True, "source_captured_at": now.isoformat(),
                     "historical_availability": "APPROXIMATED_NOT_ARCHIVED"}, created_at=now))
            stored.append(registered.id)
        test_all.extend(test)
        folds.append(fold)
    aggregate = {name: compare_metrics(moments, test_all, market) for name, moments in all_moments.items()}
    # Deliberately withhold results until the next day. This challenges the
    # historical availability approximation without selecting a favorable clock.
    delayed_history = score_history(LeagueHistory([
        replace(g, result_observed_at=g.kickoff + timedelta(hours=24))
        if g.result_observed_at else g for g in history.games], []))
    delayed_moments: dict[str, PredictedMoments] = {}
    for fold in folds:
        season = fold["test_season"]
        delayed = DynamicRatings(k=fold["selected_k"])
        delayed.fit([g for g in delayed_history.games if g.season < season and g.game_type == "REG"
                     and g.home_score is not None and g.away_score is not None], {}, delayed_history)
        delayed_moments.update(replay(delayed, [g for g in delayed_history.games if g.season == season
            and g.game_type == "REG" and g.home_score is not None and g.away_score is not None]))
    baseline = aggregate["naive-homefield-v1"]
    reports = []
    for name, metrics in aggregate.items():
        status, reasons = validation_status(metrics, baseline)
        if name == "naive-homefield-v1":
            status, reasons = "RESEARCH ONLY", ["Transparent reference baseline, not a recommendation model"]
        # Paired week-block comparison on identical games, not independent confidence intervals.
        differences, blocks = [], []
        for g in test_all:
            if g.home_score == g.away_score:
                continue
            y = int(g.home_score > g.away_score)  # type: ignore[operator]
            differences.append(loss(all_moments[name][g.id].distribution().home_win_prob(), y) -
                loss(all_moments["naive-homefield-v1"][g.id].distribution().home_win_prob(), y))
            blocks.append(f"{g.season}-{g.week}")
        ci = week_block_bootstrap(differences, blocks)
        metrics["paired_baseline_log_loss_delta_ci90"] = [ci.lo, ci.hi]
        reports.append({"name": name, "status": status, "n": metrics["n"], "metrics": metrics,
                        "reasons": reasons})
    # Current research estimates are a captured snapshot, not promoted predictions.
    future = [g for g in history.games if g.kickoff > now and g.home_score is None][:32]
    current = DynamicRatings(k=folds[-1]["selected_k"])
    current.fit(completed, {}, history)
    current.name = f"team-ratings-v1_{run_id}_current"
    current_version = register_model(session, current, target="margin+total research",
        train_window=f"captured before {now.date()}", validation_window="prior completed season",
        test_window="prospective research snapshot", feature_set="scores-only-v1", calibration_version=None,
        dataset_hashes={"games_sha256": source_hash}, metrics={},
        known_limitations="; ".join(LIMITATIONS), random_seed=20260801)
    stored.append(current_version.id)
    estimates = []
    for g in future:
        pm = current.predict(g, None)
        session.add(Prediction(id=f"{current_version.id}_{g.id}", game_id=g.id,
            model_version_id=current_version.id, feature_snapshot_id=None, horizon="RESEARCH_PREVIEW",
            as_of_at=now, outputs=pm.distribution().outputs(None, None) | {"research_only": True}, created_at=now))
        estimates.append({"canonical_game_id": g.id, "model": "team-ratings-v1", "model_version_id": current_version.id,
            "home_win_probability": pm.distribution().home_win_prob(), "margin_mean": pm.mu_margin,
            "total_mean": pm.mu_total, "margin_interval": [pm.mu_margin - 1.28155*pm.sigma_margin,
                pm.mu_margin + 1.28155*pm.sigma_margin],
            "total_interval": [pm.mu_total - 1.28155*pm.sigma_total, pm.mu_total + 1.28155*pm.sigma_total]})
    report = clean({"run_id": run_id, "created_at": now.isoformat(), "scope": "RETROSPECTIVE_RESEARCH",
        "limitations": LIMITATIONS, "models": reports, "folds": folds,
        "estimates": estimates, "registered_models": stored, "recommended_for_review": [],
        "status": "RESEARCH ONLY", "source_hash": source_hash,
        "availability_sensitivity": {"result_delay_hours": 24, "selection": "same prior-season k, no retuning",
            "team_ratings": compare_metrics(delayed_moments, test_all, market)},
        "drift": {name: [{"season": f["test_season"], "log_loss": f["models"][name]["log_loss"],
                          "mae_total": f["models"][name]["mae_total"]} for f in folds] for name in aggregate}})
    run.metrics = report
    run.status = "finished"
    run.finished_at = datetime.now(UTC)
    session.flush()
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# NFL singles research comparison", "", f"Run: `{report['run_id']}`",
        f"Recorded: {report['created_at']}", "", "RESEARCH ONLY — NO BET", "",
        "| Approach | Games | Log loss | Brier | Margin MAE | Total MAE | Status |",
        "|---|---:|---:|---:|---:|---:|---|"]
    for model in report["models"]:
        m = model["metrics"]
        lines.append(f"| {model['name']} | {model['n']} | {m['log_loss']:.4f} | {m['brier']:.4f} | "
                     f"{m['mae_margin']:.3f} | {m['mae_total']:.3f} | {model['status']} |")
    benchmark = report["models"][0]["metrics"]["closing_benchmark"]
    lines += ["", f"Closing moneyline benchmark: n={benchmark['n']}, log loss={benchmark['log_loss']:.4f}, "
              f"Brier={benchmark['brier']:.4f}. Closing margin MAE={benchmark['margin_mae']:.3f}; "
              f"total MAE={benchmark['total_mae']:.3f}.", "",
              "Closing quotes are retrospective yardsticks. No candidate receives them.", ""]
    for model in report["models"]:
        lines += [f"## {model['name']}", "", *[f"- {r}" for r in model["reasons"]], "",
            f"Log loss 90% week-block interval: {model['metrics']['log_loss_ci90']}.",
            f"80% distribution interval coverage: {model['metrics']['interval_80_coverage']}.",
            f"Calibration intercept/slope: {model['metrics']['calibration_intercept']} / "
            f"{model['metrics']['calibration_slope']}.",
            f"Paired log-loss difference vs baseline, 90% interval: "
            f"{model['metrics']['paired_baseline_log_loss_delta_ci90']}.", ""]
    lines += ["## Boundaries", "", *[f"- {r}" for r in report["limitations"]], "",
        "Full JSON retains all season folds, reliability bins, spread/total metrics, selected hyperparameters, "
        "prior-validation sensitivity, 24-hour result-delay sensitivity, and registered model identities."]
    return "\n".join(lines) + "\n"


def read_comparison(session: Session, now: datetime, game_id: str | None = None) -> dict[str, Any]:
    # No write or fitting on a request. Unknown report versions fail closed.
    for run in session.scalars(select(BacktestRun).where(BacktestRun.status == "finished",
            BacktestRun.created_at <= now).order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())):
        if run.config.get("comparison_version") != VERSION or not run.metrics:
            continue
        report = dict(run.metrics)
        created = datetime.fromisoformat(report["created_at"])
        if created.tzinfo is None or created > now:
            continue
        game = session.get(Game, game_id) if game_id else None
        future_game = game is not None and game.kickoff_utc is not None and game.home_score is None and game.away_score is None
        if future_game and game is not None and game.kickoff_utc is not None:
            kickoff = game.kickoff_utc if game.kickoff_utc.tzinfo else game.kickoff_utc.replace(tzinfo=UTC)
            future_game = kickoff > now
        report["estimates"] = [e for e in report.get("estimates", [])
                               if game_id and e["canonical_game_id"] == game_id] if (now-created).total_seconds() <= 86400 else []
        if not future_game:
            report["estimates"] = []
        return {"status": "RESEARCH ONLY", "report": report}
    return {"status": "INSUFFICIENT EVIDENCE", "report": None}
