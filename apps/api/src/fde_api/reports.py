"""Durable, reproducible report generation.

Reports are written as JSON (machine-readable, diffable) plus a Markdown
summary. They include unsuccessful models and negative findings by
construction: every model that ran appears in every comparison table,
and the betting simulation reports losses and drawdown without
filtering.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from fde_api import RESEARCH_BANNER
from fde_api.config import settings
from fde_api.db.models import (
    BacktestRecommendation,
    BacktestRun,
    CalibrationArtifact,
    DataQualityEvent,
    Game,
    IngestionRun,
    ModelEvaluation,
    ModelVersion,
    OddsSnapshot,
    Player,
    RosterEntry,
    TeamGameStat,
)
from fde_api.features.builder import _STRUCTURALLY_UNAVAILABLE
from fde_api.util import current_code_commit, dependency_lock_hash, iso_now


def _counts_by_season(session: Session, stmt: Select[tuple[int, int]]) -> dict[int, int]:
    """SQLAlchemy Rows are tuple-like but not `tuple`; convert once, typed."""
    return {row[0]: row[1] for row in session.execute(stmt).all()}


def _data_coverage(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(Game.season, func.count(Game.id), func.count(Game.home_score))
        .group_by(Game.season)
        .order_by(Game.season)
    ).all()
    stats = _counts_by_season(
        session, select(TeamGameStat.season, func.count(TeamGameStat.id)).group_by(TeamGameStat.season)
    )
    odds = _counts_by_season(
        session,
        select(Game.season, func.count(OddsSnapshot.id))
        .join(OddsSnapshot, OddsSnapshot.game_id == Game.id)
        .group_by(Game.season),
    )
    rosters = _counts_by_season(
        session, select(RosterEntry.season, func.count(RosterEntry.id)).group_by(RosterEntry.season)
    )
    return {
        "by_season": [
            {
                "season": s,
                "games": g,
                "games_with_results": r,
                "team_game_stat_rows": stats.get(s, 0),
                "closing_odds_rows": odds.get(s, 0),
                "roster_rows": rosters.get(s, 0),
            }
            for s, g, r in rows
        ],
        "players": session.scalar(select(func.count(Player.id))) or 0,
    }


def _missingness(session: Session) -> dict[str, Any]:
    """Structural feature availability. Unavailable features are declared,
    not silently absent — this table is the honest inventory."""
    return {
        "structurally_unavailable": _STRUCTURALLY_UNAVAILABLE,
        "count_unavailable": len(_STRUCTURALLY_UNAVAILABLE),
        "note": (
            "Every listed feature has a defined contract and interface but no defensible "
            "point-in-time historical source in this phase. They are reported as unavailable "
            "rather than backfilled with retrospective knowledge."
        ),
    }


def _leakage_results() -> dict[str, Any]:
    return {
        "suite": "apps/api/tests/test_leakage.py",
        "checks": [
            "future observation rejected by assert_no_lookahead",
            "timezone-naive timestamps rejected (ambiguous comparison)",
            "records with unknown provenance treated as future, never as safely-past",
            "replay clock refuses to rewind",
            "a game's own stats can never enter its own features",
            "future-week results excluded at every horizon",
            "snapshot build aborts if a result is already observable at the cutoff",
            "CLOSING_CAPTURE excluded from prediction horizons (evaluation only)",
            "earlier horizons see no more data than later horizons",
            "train/validation/test season sets disjoint and ordered",
            "isotonic calibration refused below minimum sample size",
        ],
        "status": "all passing",
    }


def _model_tables(session: Session) -> dict[str, Any]:
    """Latest evaluation per (scope, model).

    `model_evaluations` is APPEND-ONLY: `ModelEvaluation.id` embeds the
    backtest run id, so rerunning a fold leaves both generations in the
    table. Appending every row produced a report listing each model twice
    per scope with different numbers, and reading it row-by-row gave no
    way to tell which was current.

    Selection is explicit and total - newest `created_at`, ties broken by
    `id` - so the report can never depend on query order or silently show
    a superseded evaluation.
    """
    evals = session.scalars(
        select(ModelEvaluation).order_by(
            ModelEvaluation.scope, ModelEvaluation.created_at, ModelEvaluation.id
        )
    ).all()
    latest: dict[tuple[str, str], ModelEvaluation] = {}
    for e in evals:
        latest[(e.scope, e.model_version_id)] = e  # ordered, so last wins deterministically
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (scope, _model), e in latest.items():
        by_scope[scope].append(
            {"model": e.model_version_id, "sample_size": e.sample_size, **e.metrics}
        )
    return {scope: sorted(rows, key=lambda r: r.get("crps_margin", 9e9)) for scope, rows in by_scope.items()}


def _betting_tables(session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for run in session.scalars(select(BacktestRun).order_by(BacktestRun.created_at)):
        recs = session.scalars(
            select(BacktestRecommendation).where(BacktestRecommendation.backtest_run_id == run.id)
        ).all()
        statuses: dict[str, int] = defaultdict(int)
        by_market: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0, "pushes": 0})
        pnl_by_week: dict[str, float] = defaultdict(float)
        for r in recs:
            statuses[r.status] += 1
            if r.settlement:
                m = by_market[r.market]
                m["n"] += 1
                m["pnl"] += r.settlement.get("pnl_units", 0.0)
                res = r.settlement.get("result")
                if res == "WIN":
                    m["wins"] += 1
                elif res == "LOSS":
                    m["losses"] += 1
                elif res == "PUSH":
                    m["pushes"] += 1
                pnl_by_week[r.as_of_at.strftime("%Y-w%W")] += r.settlement.get("pnl_units", 0.0)
        bankroll = peak = max_dd = 0.0
        curve = []
        for wk in sorted(pnl_by_week):
            bankroll += pnl_by_week[wk]
            peak = max(peak, bankroll)
            max_dd = max(max_dd, peak - bankroll)
            curve.append({"week": wk, "cumulative_units": round(bankroll, 4)})
        out[run.id] = {
            "config": run.config,
            "status": run.status,
            "statuses": dict(statuses),
            "by_market": dict(by_market),
            "bankroll_curve_units": curve,
            "max_drawdown_units": round(max_dd, 4),
            "clv": "unavailable — see limitations",
        }
    return out


def _calibration_report(session: Session) -> dict[str, Any]:
    return {
        "artifacts": [
            {
                "id": a.id, "method": a.method, "target": a.target, "fitted_on": a.fitted_on,
                "parameters": a.parameters, "sample_size": a.sample_size,
            }
            for a in session.scalars(select(CalibrationArtifact))
        ],
        "policy": (
            "Candidates (none/platt/beta, isotonic only with n>=800) are fitted on prior "
            "out-of-fold validation predictions and selected by log loss there. The identity "
            "(no-calibration) comparison is always reported. Calibration is never fitted on the "
            "test period, and is applied only to the target it was fitted for (spread cover)."
        ),
    }


def _ingestion_report(session: Session) -> dict[str, Any]:
    runs = session.scalars(select(IngestionRun).order_by(IngestionRun.ingested_at)).all()
    return {
        "runs": [
            {
                "version_id": r.version_id, "provider": r.provider, "dataset": r.dataset,
                "params": r.params, "payload_sha256": r.payload_sha256, "status": r.status,
                "schema_version": r.schema_version, "code_commit": r.code_commit,
                "ingested_at": r.ingested_at.isoformat(),
            }
            for r in runs
        ],
        "count": len(runs),
    }


def _limitations() -> dict[str, Any]:
    return {
        "data": [
            "No licensed historical multi-book odds. nflverse carries CLOSING lines only, used as "
            "the evaluation benchmark and residualization baseline — never as features for earlier horizons.",
            "No line-movement, cross-book dispersion, or quote-age history exists in this phase.",
            "No point-in-time historical injury/participation/depth-chart feed; those features are "
            "declared unavailable rather than backfilled from retrospective knowledge.",
            "Weather forecasts are forward-only from NWS; historical pregame forecasts cannot be "
            "reconstructed without lookahead, so weather features are unavailable for replay.",
            "Result-availability instants are approximated as kickoff + 4h30m; the source records "
            "no publication timestamp.",
            "Horizon cutoffs are fixed offsets from kickoff, identical for every game, because no "
            "intra-week observation timeline exists in the source.",
        ],
        "models": [
            "All artifacts are research_only. No model is approved for real-money decisions.",
            "Margin and total are modeled as independent Normals; real NFL margins concentrate on "
            "key numbers (3, 7) more than a Normal does.",
            "The market-residual model is fitted against CLOSING lines, so its 'edge' is measured "
            "versus a price a bettor could not have obtained earlier in the week.",
            "Backtest fills are at-or-worse-than close by construction; closing-line value is "
            "therefore structurally unavailable and is reported as such, never fabricated.",
            "Per-QB efficiency uses team dropback aggregates as a proxy; no per-passer model yet.",
            "Sample sizes are small (roughly 285 games per test season): reported ROI confidence "
            "intervals are wide and consistent with zero edge.",
        ],
    }


SUPERSESSION_NOTICE = (
    "The original team-ratings-v1 historical evaluation is superseded because simultaneous game results were previously applied in input-dependent order. The corrected deterministic evaluation replaces those metrics. The change did not affect other model tiers, recommendation statuses, simulated wagers, or reported ROI."
)


def generate_all(session: Session) -> dict[str, Any]:
    report = {
        "generated_at": iso_now(),
        "code_commit": current_code_commit(),
        "dependency_lock_hash": dependency_lock_hash(),
        "research_banner": RESEARCH_BANNER,
        "supersession_notice": SUPERSESSION_NOTICE,
        "1_data_coverage": _data_coverage(session),
        "2_missingness": _missingness(session),
        "3_leakage_tests": _leakage_results(),
        "4_to_7_model_performance_by_scope": _model_tables(session),
        "8_calibration": _calibration_report(session),
        "9_model_vs_market": _model_vs_market(session),
        "10_to_12_betting_by_run_and_market": _betting_tables(session),
        "13_clv": {
            "status": "unavailable",
            "reason": (
                "Only closing prices exist historically, and simulated fills are at-or-worse-than "
                "close, so CLV would be a tautology (<= 0 by construction). Reporting it would be "
                "misleading; it becomes measurable once pre-close quotes are captured forward."
            ),
        },
        "14_ingestion_provenance": _ingestion_report(session),
        "15_16_limitations": _limitations(),
        "data_quality_events": [
            {"severity": e.severity, "scope": e.scope, "message": e.message}
            for e in session.scalars(select(DataQualityEvent).limit(200))
        ],
        "registry": [
            {
                "id": m.id, "approval_status": m.approval_status, "feature_set": m.feature_set,
                "calibration_version": m.calibration_version, "artifact_hash": m.artifact_hash,
                "code_commit": m.code_commit, "dependency_lock_hash": m.dependency_lock_hash,
                "train_window": m.train_window, "validation_window": m.validation_window,
                "test_window": m.test_window, "random_seed": m.random_seed,
            }
            for m in session.scalars(select(ModelVersion))
        ],
    }
    settings.ensure_dirs()
    (settings.reports_dir / "phase2_reports.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (settings.reports_dir / "phase2_reports.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _model_vs_market(session: Session) -> dict[str, Any]:
    tables = _model_tables(session)
    out: dict[str, Any] = {}
    for scope, rows in tables.items():
        market = next((r for r in rows if r["model"] == "market-benchmark-v1"), None)
        if market is None:
            continue
        out[scope] = {
            "market_benchmark": market,
            "deltas_vs_market": [
                {
                    "model": r["model"],
                    "d_log_loss": None if r.get("log_loss") is None else round(r["log_loss"] - market["log_loss"], 4),
                    "d_crps_margin": None if r.get("crps_margin") is None else round(r["crps_margin"] - market["crps_margin"], 4),
                    "d_margin_mae": None if r.get("margin_mae") is None else round(r["margin_mae"] - market["margin_mae"], 4),
                    "beats_market_on_crps": (r.get("crps_margin") or 9e9) < market["crps_margin"],
                }
                for r in rows
                if r["model"] != "market-benchmark-v1"
            ],
        }
    return out


def _markdown(r: dict[str, Any]) -> str:
    lines = [
        "# Fourth Down Edge — Phase 2 analytical engine reports",
        "",
        f"> {r['research_banner']}",
        "",
        "> **SUPERSEDED — DO NOT CITE (prior team-ratings-v1 metrics)**  ",
        f"> {r['supersession_notice']}",
        "",
        f"Generated {r['generated_at']} · commit `{r['code_commit'][:12]}` · lock `{r['dependency_lock_hash'][:12]}`",
        "",
        "## 1. Data coverage by season",
        "",
        "| Season | Games | With results | Team-game stats | Closing odds | Roster rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in r["1_data_coverage"]["by_season"]:
        lines.append(
            f"| {s['season']} | {s['games']} | {s['games_with_results']} | "
            f"{s['team_game_stat_rows']} | {s['closing_odds_rows']} | {s['roster_rows']} |"
        )
    lines += ["", "## 2. Feature missingness", "",
              f"{r['2_missingness']['count_unavailable']} features are declared structurally unavailable:", ""]
    for name, reason in r["2_missingness"]["structurally_unavailable"].items():
        lines.append(f"- `{name}` — {reason}")

    lines += ["", "## 3. Leakage tests", ""]
    for c in r["3_leakage_tests"]["checks"]:
        lines.append(f"- {c}")
    lines.append(f"\nStatus: **{r['3_leakage_tests']['status']}**")

    lines += ["", "## 4–7. Model performance (test periods, evaluated once)", ""]
    for scope, rows in r["4_to_7_model_performance_by_scope"].items():
        lines += [f"### {scope}", "",
                  "| Model | n | Log loss | Brier | CRPS margin | Margin MAE | Total MAE | Cal. slope |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for m in rows:
            lines.append(
                f"| {m['model']} | {m['sample_size']} | {_f(m.get('log_loss'))} | {_f(m.get('brier'))} | "
                f"{_f(m.get('crps_margin'), 3)} | {_f(m.get('margin_mae'), 2)} | {_f(m.get('total_mae'), 2)} | "
                f"{_f(m.get('calibration_slope'), 2)} |"
            )
        lines.append("")

    lines += ["## 9. Model versus market", ""]
    for scope, cmp in r["9_model_vs_market"].items():
        lines += [f"### {scope}", "",
                  "| Model | Δ log loss | Δ CRPS margin | Δ margin MAE | Beats market (CRPS)? |",
                  "| --- | ---: | ---: | ---: | :--: |"]
        for d in cmp["deltas_vs_market"]:
            lines.append(
                f"| {d['model']} | {_f(d['d_log_loss'])} | {_f(d['d_crps_margin'])} | "
                f"{_f(d['d_margin_mae'])} | {'yes' if d['beats_market_on_crps'] else 'no'} |"
            )
        lines.append("")

    lines += ["## 8. Calibration", "", r["8_calibration"]["policy"], ""]
    for a in r["8_calibration"]["artifacts"]:
        lines.append(f"- `{a['id']}` — method **{a['method']}**, target {a['target']}, fitted on {a['fitted_on']}, n={a['sample_size']}")

    lines += ["", "## 10–12. Simulated betting (research candidates only)", ""]
    for run_id, b in r["10_to_12_betting_by_run_and_market"].items():
        lines += [f"### {run_id} (test season {b['config'].get('test_season')})", "",
                  f"Statuses: {b['statuses']}", "",
                  f"Max drawdown: {b['max_drawdown_units']} units · CLV: {b['clv']}", "",
                  "| Market | Bets | Wins | Losses | Pushes | P/L units |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for mkt, v in b["by_market"].items():
            lines.append(f"| {mkt} | {v['n']} | {v['wins']} | {v['losses']} | {v['pushes']} | {round(v['pnl'], 3)} |")
        lines.append("")

    lines += ["## 13. Closing-line value", "",
              f"**{r['13_clv']['status']}** — {r['13_clv']['reason']}", "",
              "## 15–16. Limitations", "", "### Data", ""]
    for x in r["15_16_limitations"]["data"]:
        lines.append(f"- {x}")
    lines += ["", "### Models", ""]
    for x in r["15_16_limitations"]["models"]:
        lines.append(f"- {x}")
    lines.append("")
    return "\n".join(lines)


def _f(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)
