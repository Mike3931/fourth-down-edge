"""Expanding-window walk-forward validation with weekly replay.

Fold design for test season N:
    train    seasons ≤ N−2
    validate season N−1   (all selection happens here)
    test     season N     (evaluated once, never re-optimized)

Selected on the validation season only: feature decay half-life, ridge
alphas, ratings k, calibration method, and the research-candidate edge
threshold. The test season is then replayed week by week on a forward-
only clock.

Evaluation integrity: models that update in-season (the ratings model)
are ALWAYS evaluated from the moments they produced at replay time —
never re-queried after later results have been observed. The moments
captured during replay are the single source for metrics, stored
predictions, and the betting simulation alike.

Statuses emitted are research-grade only (RESEARCH_CANDIDATE / WATCH /
PASS / DATA_INCOMPLETE) and every opportunity is retained append-only,
including passes.

CLV note: with closing prices as the only historical quotes, simulated
fills are at-or-worse-than close by construction, so CLV is reported as
structurally unavailable rather than fabricated.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.backtest.execution import (
    ExecutionConfig,
    ExecutionModel,
    Selection,
    break_even_prob,
)
from fde_api.calibration import FittedCalibration, select_calibration
from fde_api.config import settings
from fde_api.db.models import (
    BacktestRecommendation,
    BacktestRun,
    CalibrationArtifact,
    ModelEvaluation,
    OddsSnapshot,
    Prediction,
)
from fde_api.evaluation import (
    brier as brier_score,
)
from fde_api.evaluation import (
    calibration_line,
    crps_normal,
    log_loss,
    mae,
    reliability_bins,
    week_block_bootstrap,
)
from fde_api.features import CoreV1Builder, LeagueHistory
from fde_api.features.builder import FEATURE_SET_VERSION, DecayConfig
from fde_api.features.history import GameRow
from fde_api.models_ml.baselines import HomeFieldBaseline, RollingAverageBaseline
from fde_api.models_ml.glm import MarketResidual, RidgeBaseline
from fde_api.models_ml.market import MarketBenchmark
from fde_api.models_ml.protocol import GamePredictor, MarketRef, PredictedMoments
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.pit.clock import PredictionHorizon, ReplayClock, horizon_as_of
from fde_api.pit.guards import LookaheadError
from fde_api.registry import register_model
from fde_api.util import utc_now

MomentsFn = Callable[[GameRow], PredictedMoments | None]


@dataclass
class WalkForwardConfig:
    test_season: int
    horizon: PredictionHorizon = PredictionHorizon.PREGAME
    half_life_grid: tuple[float, ...] = (180.0, 365.0)
    ridge_alpha_grid: tuple[float, ...] = (10.0, 25.0, 60.0)
    ratings_k_grid: tuple[float, ...] = (0.08, 0.12, 0.18)
    candidate_edge_grid: tuple[float, ...] = (0.03, 0.05, 0.08)
    watch_margin: float = 0.015
    seed: int = 20260801
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


# ------------------------------------------------------------------------- #
# Feature cache
# ------------------------------------------------------------------------- #


def _feature_cache_path(half_life: float, horizon: PredictionHorizon) -> Any:
    settings.ensure_dirs()
    return settings.artifacts_dir / f"features_{FEATURE_SET_VERSION}_hl{int(half_life)}_{horizon.value}.json"


FEATURE_CACHE_FORMAT = 2


def build_or_load_features(
    history: LeagueHistory, half_life: float, horizon: PredictionHorizon
) -> dict[str, dict[str, Any]]:
    """Feature snapshots for every game, cached on disk.

    The cache is keyed by feature-set version, half-life and horizon —
    nothing about the DATA. That was silently wrong the moment the data
    changed: re-ingesting a game with a corrected score, or adding a
    season, left the file untouched and every later run read features
    derived from superseded rows. The values stay entirely plausible, so
    no downstream check could notice.

    The file now carries the fingerprint of the history it was built from
    and is rebuilt when that no longer matches. Files written by the
    previous format have no fingerprint and are treated as stale, which
    costs one rebuild and cannot change a result: the same data produces
    the same features.
    """
    path = _feature_cache_path(half_life, horizon)
    fingerprint = history.fingerprint()
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("format") == FEATURE_CACHE_FORMAT
            and cached.get("history_fingerprint") == fingerprint
        ):
            return cast(dict[str, dict[str, Any]], cached["features"])

    builder = CoreV1Builder(history, DecayConfig(half_life_days=half_life))
    out: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for g in history.games:
        try:
            snap = builder.build(g, horizon)
        except Exception:
            # A snapshot can be genuinely impossible — the first games of
            # the earliest season have no prior history to decay. Recorded
            # rather than merely skipped, so a build that starts dropping
            # games leaves a trace instead of a quietly smaller dataset.
            skipped.append(g.id)
            continue
        out[g.id] = snap.values
    path.write_text(
        json.dumps(
            {
                "format": FEATURE_CACHE_FORMAT,
                "feature_set": FEATURE_SET_VERSION,
                "half_life_days": half_life,
                "horizon": horizon.value,
                "history_fingerprint": fingerprint,
                "games_covered": len(out),
                "games_skipped": skipped,
                "features": out,
            },
            default=str,
        ),
        encoding="utf-8",
    )
    return out


class ConflictingMarketReference(ValueError):
    """Two closing benchmarks for one game and market that disagree."""


def load_market_refs(session: Session) -> tuple[dict[str, MarketRef], dict[str, dict[str, Any]]]:
    """The market reference every model is measured against.

    `market-benchmark-v1` IS this number and `market-residual-v1` predicts
    a correction to it, so a wrong value here does not degrade one model,
    it moves the yardstick and every score reported against it.

    The loop below assigns by key, so where a (game, market) has more than
    one benchmark row the last one read wins. That is currently harmless
    and entirely invisible: the database holds exactly two rows for every
    one of 6,681 (game, market) pairs — the historical ingest is not
    idempotent — and all of them agree on every field, so last-write-wins
    picks an identical value. Nothing checked that, and nothing would have
    noticed it changing. Row order out of a bare SELECT is not guaranteed,
    so the day two benchmarks disagree the yardstick becomes whichever the
    database happened to hand over last.

    Refusing is the right response rather than picking, averaging, or
    taking the newest: a benchmark is an observation, and two conflicting
    observations of one closing market are a data question, not a
    modelling one.
    """
    refs: dict[str, dict[str, Any]] = {}
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    conflicts: list[str] = []

    def claim(game_id: str, market: str, values: dict[str, Any]) -> bool:
        """True when these values may be written; records a conflict if a
        previous row for the same (game, market) said something else."""
        key = (game_id, market)
        prior = seen.get(key)
        if prior is None:
            seen[key] = values
            return True
        if prior != values:
            conflicts.append(f"{game_id}/{market}: {prior} vs {values}")
        return False

    for snap in session.scalars(select(OddsSnapshot).where(OddsSnapshot.snapshot_kind == "CLOSING_BENCHMARK")):
        r = refs.setdefault(snap.game_id, {})
        if snap.market == "SPREAD":
            values = {
                "home_line": snap.line,
                "spread_home_price": snap.home_price_american,
                "spread_away_price": snap.away_price_american,
            }
        elif snap.market == "TOTAL":
            values = {
                "total_line": snap.line,
                "over_price": snap.over_price_american,
                "under_price": snap.under_price_american,
            }
        elif snap.market == "MONEYLINE":
            values = {
                "home_ml": snap.home_price_american,
                "away_ml": snap.away_price_american,
            }
        else:
            continue
        if claim(snap.game_id, snap.market, values):
            r.update(values)

    if conflicts:
        shown = "; ".join(sorted(conflicts)[:5])
        raise ConflictingMarketReference(
            f"{len(conflicts)} closing benchmark(s) disagree with a duplicate "
            f"of themselves, so the market reference would depend on row "
            f"order: {shown}"
        )

    return {
        gid: MarketRef(
            home_line=v.get("home_line"),
            total_line=v.get("total_line"),
            home_ml=v.get("home_ml"),
            away_ml=v.get("away_ml"),
        )
        for gid, v in refs.items()
    }, refs


# ------------------------------------------------------------------------- #
# Moments providers
# ------------------------------------------------------------------------- #


def static_moments_fn(
    model: GamePredictor, features: dict[str, dict[str, Any]], market: dict[str, MarketRef]
) -> MomentsFn:
    """For models whose state does not change in-season."""

    def fn(g: GameRow) -> PredictedMoments | None:
        try:
            return model.predict(g, features.get(g.id), market.get(g.id) if model.uses_market else None)
        except ValueError:
            return None

    return fn


def sequential_ratings_moments(
    ratings: DynamicRatings, games: list[GameRow]
) -> dict[str, PredictedMoments]:
    """Predict-then-observe over games in observation order on a forward-only
    clock — the only honest way to evaluate an in-season-updating model.

    Three independent layers keep a game's own outcome out of its own
    prediction. None of them relies on `RESULT_AVAILABILITY_OFFSET`:

    1. STRICT VISIBILITY. Results are tested with `can_see_result`
       (`observed_at < now`), not the inclusive `can_see`. At `now` equal
       to a game's kickoff, a result stamped at that same instant is not
       visible. This alone defeats a zero offset.
    2. EXPLICIT SELF-EXCLUSION. The game about to be predicted is skipped
       by id, regardless of any timestamp.
    3. INVARIANT VALIDATION. A record whose result is stamped at or before
       its own kickoff is malformed; it raises rather than being silently
       tolerated, because such a row means the ingestion guard was bypassed.

    A leak here would not crash or look implausible. It would quietly
    inflate the measured skill of the ratings model on the test season -
    the one number the walk-forward exists to produce, measured once.
    """
    for g in games:
        if g.result_observed_at is not None and g.result_observed_at <= g.kickoff:
            raise LookaheadError(
                f"game {g.id} reports its result at {g.result_observed_at.isoformat()}, "
                f"at or before its own kickoff {g.kickoff.isoformat()}; a sequential "
                "replay would observe the outcome before predicting the game"
            )

    clock = ReplayClock(min(g.kickoff for g in games))
    out: dict[str, PredictedMoments] = {}

    # Ordering is part of the result, not an implementation detail.
    # `observe_game` mutates the ratings and is NOT commutative, so two
    # games sharing a kickoff instant are absorbed in whatever order they
    # are iterated - and every later prediction inherits that state. Sorting
    # by kickoff alone is stable, which means input list order leaked into
    # the output: the same games in a different order produced different
    # predictions for subsequent games.
    #
    # Both orders below are therefore total and derived only from the data:
    # prediction order by (kickoff, id), observation order by
    # (result_observed_at, id). The replay is now a pure function of the
    # game set, independent of how it was handed in.
    pending = sorted(games, key=lambda g: (g.kickoff, g.id))
    observable = sorted(
        (g for g in games if g.result_observed_at is not None),
        key=lambda g: (g.result_observed_at, g.id),  # type: ignore[arg-type,return-value]
    )
    observed: set[str] = set()
    for g in pending:
        clock.advance_to(g.kickoff)
        # observe any game whose result became visible before this kickoff
        for o in observable:
            if o.id == g.id:
                continue  # layer 2: never the game about to be predicted
            # layer 1: strict, so a result stamped at this instant is not
            # yet visible - including a simultaneous kickoff's result.
            if o.id not in observed and o.result_observed_at and clock.can_see_result(o.result_observed_at):
                ratings.observe_game(o)
                observed.add(o.id)
        out[g.id] = ratings.predict(g, None)
    return out


def precomputed_moments_fn(moments: dict[str, PredictedMoments]) -> MomentsFn:
    return lambda g: moments.get(g.id)


# ------------------------------------------------------------------------- #
# Scoring
# ------------------------------------------------------------------------- #


def _win_outcome(g: GameRow) -> int | None:
    if g.home_score is None or g.away_score is None or g.home_score == g.away_score:
        return None
    return 1 if g.home_score > g.away_score else 0


def score_moments(
    moments_fn: MomentsFn,
    games: list[GameRow],
    calibration: FittedCalibration | None = None,
) -> dict[str, Any]:
    win_p: list[float] = []
    win_y: list[int] = []
    weeks: list[str] = []
    crps_m: list[float] = []
    crps_t: list[float] = []
    margin_pred: list[float] = []
    margin_act: list[float] = []
    total_pred: list[float] = []
    total_act: list[float] = []
    for g in games:
        if g.home_score is None or g.away_score is None:
            continue
        pm = moments_fn(g)
        if pm is None:
            continue
        dist = pm.distribution()
        y = _win_outcome(g)
        if y is not None:
            p = dist.home_win_prob()
            if calibration is not None:
                p = calibration.apply(p)
            win_p.append(p)
            win_y.append(y)
            weeks.append(f"{g.season}w{g.week}")
        margin = g.home_score - g.away_score
        total = g.home_score + g.away_score
        crps_m.append(crps_normal(pm.mu_margin, pm.sigma_margin, margin))
        crps_t.append(crps_normal(pm.mu_total, pm.sigma_total, total))
        margin_pred.append(pm.mu_margin)
        margin_act.append(margin)
        total_pred.append(pm.mu_total)
        total_act.append(total)
    if not margin_pred:
        return {"n": 0}
    intercept, slope = calibration_line(win_p, win_y) if len(win_p) > 30 else (float("nan"), float("nan"))
    ll = log_loss(win_p, win_y) if win_p else float("nan")
    ll_ci = (
        week_block_bootstrap(
            [-(math.log(max(p, 1e-9)) if y else math.log(max(1 - p, 1e-9)))
             for p, y in zip(win_p, win_y, strict=True)],
            weeks,
        )
        if win_p
        else None
    )
    return {
        "n": len(margin_pred),
        "n_win_scored": len(win_p),
        "log_loss": ll,
        "log_loss_ci90": [ll_ci.lo, ll_ci.hi] if ll_ci else None,
        "brier": brier_score(win_p, win_y) if win_p else None,
        "calibration_intercept": intercept,
        "calibration_slope": slope,
        "reliability": reliability_bins(win_p, win_y) if win_p else [],
        "crps_margin": sum(crps_m) / len(crps_m),
        "crps_total": sum(crps_t) / len(crps_t),
        "margin_mae": mae(margin_pred, margin_act),
        "total_mae": mae(total_pred, total_act),
        "win_rate_observed": sum(win_y) / len(win_y) if win_y else None,
    }


# ------------------------------------------------------------------------- #
# Betting simulation (research candidates only)
# ------------------------------------------------------------------------- #


def simulate_bets(
    moments_fn: MomentsFn,
    games: list[GameRow],
    market: dict[str, MarketRef],
    raw_prices: dict[str, dict[str, Any]],
    calibration: FittedCalibration | None,
    min_edge: float,
    watch_margin: float,
    execution: ExecutionModel,
) -> dict[str, Any]:
    ledger: list[dict[str, Any]] = []
    statuses = {"RESEARCH_CANDIDATE": 0, "WATCH": 0, "PASS": 0, "DATA_INCOMPLETE": 0}
    recommendations: list[dict[str, Any]] = []

    for g in games:
        ref = market.get(g.id)
        prices = raw_prices.get(g.id, {})
        as_of = horizon_as_of(g.kickoff, PredictionHorizon.PREGAME)
        base = {"game_id": g.id, "as_of_at": as_of.isoformat()}
        pm = moments_fn(g)
        if ref is None or ref.home_line is None or ref.total_line is None or pm is None:
            statuses["DATA_INCOMPLETE"] += 1
            recommendations.append(
                base | {"market": "ALL", "status": "DATA_INCOMPLETE",
                        "reasons": ["missing market reference or feature snapshot"]}
            )
            continue
        dist = pm.distribution()

        candidates: list[tuple[str, Selection, float, int, float]] = []
        cover, _, lose = dist.spread_probs(ref.home_line)
        candidates.append(("SPREAD", "HOME", ref.home_line, prices.get("spread_home_price") or -110, cover))
        candidates.append(("SPREAD", "AWAY", ref.home_line, prices.get("spread_away_price") or -110, lose))
        over, _, under = dist.total_probs(ref.total_line)
        candidates.append(("TOTAL", "OVER", ref.total_line, prices.get("over_price") or -110, over))
        candidates.append(("TOTAL", "UNDER", ref.total_line, prices.get("under_price") or -110, under))

        for mkt, sel, line, price, p_raw in candidates:
            p = calibration.apply(p_raw) if calibration is not None else p_raw
            be = break_even_prob(price)
            edge = p - be
            rec = base | {"market": mkt, "selection": sel, "line": line, "price": price,
                          "model_prob": p, "break_even": be, "edge": edge}
            if edge >= min_edge:
                fill = execution.attempt_fill(
                    game_id=g.id, market=mkt, selection=sel, line=line, price_american=price
                )
                if not fill.filled:
                    rec |= {"status": "PASS", "reasons": [f"no fill: {fill.reason}"]}
                    statuses["PASS"] += 1
                else:
                    settle = ExecutionModel.settle(
                        market=mkt, selection=sel, line=fill.line,
                        price_american=fill.price_american or price,
                        home_score=g.home_score, away_score=g.away_score,
                    )
                    rec |= {
                        "status": "RESEARCH_CANDIDATE",
                        "reasons": [f"calibrated edge {edge:.3f} >= {min_edge}"],
                        "execution": {"line": fill.line, "price": fill.price_american, "note": fill.reason},
                        "settlement": {"result": settle.result, "pnl_units": settle.pnl_units},
                    }
                    statuses["RESEARCH_CANDIDATE"] += 1
                    ledger.append({"week": f"{g.season}w{g.week}", "pnl": settle.pnl_units,
                                   "result": settle.result})
            elif edge >= min_edge - watch_margin:
                rec |= {"status": "WATCH", "reasons": [f"edge {edge:.3f} within watch band"]}
                statuses["WATCH"] += 1
            else:
                rec |= {"status": "PASS", "reasons": [f"edge {edge:.3f} below threshold"]}
                statuses["PASS"] += 1
            recommendations.append(rec)

    pnl = [t["pnl"] for t in ledger]
    weeks = [t["week"] for t in ledger]
    bankroll = peak = max_dd = 0.0
    for t in ledger:
        bankroll += t["pnl"]
        peak = max(peak, bankroll)
        max_dd = max(max_dd, peak - bankroll)
    roi_ci = week_block_bootstrap(pnl, weeks) if len(pnl) >= 5 else None
    return {
        "statuses": statuses,
        "n_bets": len(ledger),
        "wins": sum(1 for t in ledger if t["result"] == "WIN"),
        "losses": sum(1 for t in ledger if t["result"] == "LOSS"),
        "pushes": sum(1 for t in ledger if t["result"] == "PUSH"),
        "total_pnl_units": sum(pnl),
        "roi_per_bet": (sum(pnl) / len(pnl)) if pnl else None,
        "roi_ci90": [roi_ci.lo, roi_ci.hi] if roi_ci else None,
        "max_drawdown_units": max_dd,
        "clv": "unavailable: only closing prices exist historically; fills are at-or-worse-than close",
        "recommendations": recommendations,
    }


# ------------------------------------------------------------------------- #
# Driver
# ------------------------------------------------------------------------- #


def run_walkforward(session: Session, config: WalkForwardConfig) -> dict[str, Any]:
    run_id = f"bt_{uuid.uuid4().hex[:12]}"
    run = BacktestRun(
        id=run_id,
        config={
            "test_season": config.test_season,
            "horizon": config.horizon.value,
            "half_life_grid": list(config.half_life_grid),
            "ridge_alpha_grid": list(config.ridge_alpha_grid),
            "ratings_k_grid": list(config.ratings_k_grid),
            "candidate_edge_grid": list(config.candidate_edge_grid),
            "seed": config.seed,
            "execution": vars(config.execution),
        },
        status="running",
        created_at=utc_now(),
    )
    session.add(run)
    session.flush()

    history = LeagueHistory.load(session)
    market, raw_prices = load_market_refs(session)

    n = config.test_season
    train = [g for g in history.games if g.season <= n - 2]
    val = [g for g in history.games if g.season == n - 1]
    test = [g for g in history.games if g.season == n]
    if not train or not val or not test:
        run.status = "failed"
        run.error = f"insufficient seasons for test_season={n}"
        session.flush()
        raise ValueError(run.error)

    # ---- selection on validation season only -------------------------- #
    selection_report: dict[str, Any] = {}

    best_hl, best_alpha, best_crps = None, None, float("inf")
    for hl in config.half_life_grid:
        feats = build_or_load_features(history, hl, config.horizon)
        for alpha in config.ridge_alpha_grid:
            ridge = RidgeBaseline(alpha=alpha, seed=config.seed)
            ridge.fit(train, feats, history)
            s = score_moments(static_moments_fn(ridge, feats, market), val)
            if s.get("n", 0) and s["crps_margin"] < best_crps:
                best_hl, best_alpha, best_crps = hl, alpha, s["crps_margin"]
    selection_report["ridge"] = {"half_life": best_hl, "alpha": best_alpha, "val_crps_margin": best_crps}
    features = build_or_load_features(history, best_hl or 365.0, config.horizon)

    best_k, best_k_crps = None, float("inf")
    for k in config.ratings_k_grid:
        rating_candidate = DynamicRatings(k=k)
        rating_candidate.fit(train, features, history)
        val_moments = sequential_ratings_moments(rating_candidate, val)
        s = score_moments(precomputed_moments_fn(val_moments), val)
        if s.get("n", 0) and s["crps_margin"] < best_k_crps:
            best_k, best_k_crps = k, s["crps_margin"]
    selection_report["ratings"] = {"k": best_k, "val_crps_margin": best_k_crps}

    # Calibration for the residual model's cover probabilities: fit on the
    # VALIDATION season's out-of-fold cover predictions (prior to test).
    resid_for_cal = MarketResidual(seed=config.seed)
    resid_for_cal.fit(train, features, history, market)
    cal_p: list[float] = []
    cal_y: list[int] = []
    for g in val:
        ref = market.get(g.id)
        if ref is None or ref.home_line is None or g.home_score is None or g.away_score is None:
            continue
        try:
            pm = resid_for_cal.predict(g, features.get(g.id), ref)
        except ValueError:
            continue
        cover, _push, _ = pm.distribution().spread_probs(ref.home_line)
        actual = (g.home_score - g.away_score) + ref.home_line
        if abs(actual) < 1e-9:
            continue
        cal_p.append(cover)
        cal_y.append(1 if actual > 0 else 0)
    calibration, cal_scores = select_calibration(cal_p, cal_y)
    selection_report["calibration"] = {"chosen": calibration.method, "candidate_log_loss": cal_scores,
                                       "fitted_on": f"val:{n-1}", "n": calibration.sample_size}

    # Edge threshold: chosen on validation-season simulated ROI.
    execution = ExecutionModel(config.execution)
    resid_val_fn = static_moments_fn(resid_for_cal, features, market)
    best_edge, best_roi = config.candidate_edge_grid[0], -float("inf")
    for e in config.candidate_edge_grid:
        sim = simulate_bets(resid_val_fn, val, market, raw_prices, calibration, e,
                            config.watch_margin, execution)
        roi = sim["roi_per_bet"] if sim["roi_per_bet"] is not None else -float("inf")
        if sim["n_bets"] >= 20 and roi > best_roi:
            best_edge, best_roi = e, roi
    selection_report["edge_threshold"] = {"chosen": best_edge, "val_roi_per_bet": best_roi}

    # ---- final fits on train+val ------------------------------------- #
    fit_games = train + val
    models: dict[str, GamePredictor] = {
        "naive-homefield-v1": HomeFieldBaseline(),
        "naive-rolling-v1": RollingAverageBaseline(),
        "market-benchmark-v1": MarketBenchmark(),
        "team-ratings-v1": DynamicRatings(k=best_k or 0.12),
        "glm-ridge-v1": RidgeBaseline(alpha=best_alpha or 25.0, seed=config.seed),
        "market-residual-v1": MarketResidual(seed=config.seed),
    }
    for model in models.values():
        model.fit(fit_games, features, history, market)

    # Register every fitted artifact (research_only) before any prediction
    # rows exist — the predictions FK makes unregistered models unstorable.
    ing = session.execute(
        select(OddsSnapshot.source_manifest_version)
        .where(OddsSnapshot.source_manifest_version.isnot(None))
        .limit(1)
    ).scalar()
    dataset_hashes = {"games_manifest": ing or "unknown"}
    cal_version = f"cal_{calibration.method}_val{n-1}"
    session.merge(
        CalibrationArtifact(
            id=cal_version,
            method=calibration.method,
            target="spread_cover_prob",
            fitted_on=f"val OOF season {n-1}",
            parameters=calibration.parameters,
            sample_size=calibration.sample_size,
            created_at=utc_now(),
        )
    )
    for name, model in models.items():
        register_model(
            session,
            model,
            target="margin+total joint distribution",
            train_window=f"seasons<= {n-2}",
            validation_window=f"season {n-1}",
            test_window=f"season {n}",
            feature_set=FEATURE_SET_VERSION if isinstance(model, RidgeBaseline | MarketResidual) else None,
            calibration_version=cal_version if name == "market-residual-v1" else None,
            dataset_hashes=dataset_hashes,
            metrics={},
            known_limitations=(
                "Research only. Trained on nflverse-derived data with approximated "
                "observation instants; market inputs limited to closing benchmarks."
            ),
            random_seed=config.seed,
        )

    # ---- weekly replay over the test season --------------------------- #
    # Moments captured HERE are the single evaluation source. The ratings
    # model predicts each game before observing its result (sequential),
    # so no test-season information flows backward.
    replay_moments: dict[str, dict[str, PredictedMoments]] = {name: {} for name in models}
    ratings = models["team-ratings-v1"]
    assert isinstance(ratings, DynamicRatings)
    replay_moments["team-ratings-v1"] = sequential_ratings_moments(ratings, test)
    for name, model in models.items():
        if name == "team-ratings-v1":
            continue
        fn = static_moments_fn(model, features, market)
        for g in test:
            pm = fn(g)
            if pm is not None:
                replay_moments[name][g.id] = pm

    predictions_stored = 0
    for name in models:
        for g in test:
            pm = replay_moments[name].get(g.id)
            if pm is None:
                continue
            as_of = horizon_as_of(g.kickoff, config.horizon)
            ref = market.get(g.id)
            session.merge(
                Prediction(
                    id=f"pred_{g.id}_{name}_{config.horizon.value}",
                    game_id=g.id,
                    model_version_id=name,
                    feature_snapshot_id=None,
                    horizon=config.horizon.value,
                    as_of_at=as_of,
                    outputs=pm.distribution().outputs(
                        ref.home_line if ref else None, ref.total_line if ref else None
                    )
                    | {"components": pm.components},
                    created_at=utc_now(),
                )
            )
            predictions_stored += 1

    # ---- one-shot test evaluation from replay moments ------------------ #
    # Win-prob metrics are always UNCALIBRATED here: the calibration artifact
    # targets spread-cover probabilities and is applied only where it was
    # fitted to apply — inside the betting simulation.
    metrics: dict[str, Any] = {}
    for name in models:
        metrics[name] = score_moments(precomputed_moments_fn(replay_moments[name]), test)
        session.add(
            ModelEvaluation(
                id=f"ev_{run_id}_{name}",
                model_version_id=name,
                scope=f"test:{n}:{config.horizon.value}",
                metrics={k: v for k, v in metrics[name].items() if k != "reliability"},
                sample_size=metrics[name].get("n", 0),
                created_at=utc_now(),
            )
        )

    bets = simulate_bets(
        precomputed_moments_fn(replay_moments["market-residual-v1"]), test, market, raw_prices,
        calibration, best_edge, config.watch_margin, execution,
    )
    for rec in bets["recommendations"]:
        session.add(
            BacktestRecommendation(
                backtest_run_id=run_id,
                game_id=rec["game_id"],
                prediction_id=None,
                market=rec.get("market", "ALL"),
                selection=rec.get("selection"),
                line=rec.get("line"),
                price_american=rec.get("price"),
                status=rec["status"],
                reasons={"reasons": rec.get("reasons", []),
                         "edge": rec.get("edge"), "model_prob": rec.get("model_prob")},
                execution=rec.get("execution"),
                settlement=rec.get("settlement"),
                as_of_at=datetime.fromisoformat(rec["as_of_at"]),
                created_at=utc_now(),
            )
        )

    summary = {
        "run_id": run_id,
        "fold": {"train": f"<= {n-2}", "validate": n - 1, "test": n},
        "selection": selection_report,
        "test_metrics": {k: {kk: vv for kk, vv in v.items() if kk != "reliability"} for k, v in metrics.items()},
        "reliability": {k: v.get("reliability", []) for k, v in metrics.items()},
        "betting_simulation": {k: v for k, v in bets.items() if k != "recommendations"},
        "predictions_stored": predictions_stored,
    }
    run.status = "finished"
    run.metrics = {k: v for k, v in summary.items() if k != "reliability"}
    run.finished_at = utc_now()
    session.flush()
    return summary
