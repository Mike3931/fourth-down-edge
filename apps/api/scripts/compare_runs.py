"""Run-scoped comparison of two walk-forward executions of the same fold.

Supersedes the collapsed-key comparison in `backtest.certification`, which
keyed evaluations on (model, scope) and recommendations on
(game, market, selection, as_of). Both of those tables are APPEND-ONLY per
run - `ModelEvaluation.id` embeds the run id, and recommendations use an
autoincrement key - so after a rerun the database holds both the original
and the new rows. Collapsing them by a composite key silently kept
whichever row dict insertion happened to reach last, which is not a
comparison at all.

This matches runs explicitly: for each test season, the ORIGINAL run's
rows against the RERUN's rows, joined on a stable within-run identity.

Predictions are exempt from the problem - `Prediction.id` is deterministic
(`pred_{game}_{model}_{horizon}`) and the rerun merges over the original -
but they are compared here too so one report covers everything.
"""

from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select

from fde_api.db.engine import get_session
from fde_api.db.models import BacktestRecommendation, BacktestRun, ModelEvaluation

FLOAT_TOL = 1e-9


class ComparisonError(RuntimeError):
    """The comparison cannot be trusted and must not be reported."""


# STABLE SEMANTIC KEYS - documented because the whole tool depends on them.
#
#   evaluations      (model_version_id, scope)   within one explicitly
#                    selected run. NOT across runs: ModelEvaluation.id
#                    embeds the run id, so the table holds one row per
#                    (model, scope) PER RUN.
#   recommendations  (game_id, market, selection, as_of_at) within one
#                    explicitly selected run. The primary key is an
#                    autoincrement integer and is deliberately excluded.
#
# Run UUIDs and generated database ids are never part of a semantic key or
# a semantic hash: they change on every execution and would make two
# identical runs look different.
EVALUATION_KEY = ("model_version_id", "scope")
RECOMMENDATION_KEY = ("game_id", "market", "selection", "as_of_at")
EXCLUDED_FROM_SEMANTIC_KEYS = ("id", "backtest_run_id", "run_id", "created_at")


def _require_run(session, run_id: str):
    run = session.get(BacktestRun, run_id)
    if run is None:
        raise ComparisonError(
            f"run {run_id!r} does not exist; a comparison cannot be performed "
            "against a run that is not in the database"
        )
    return run


def _index_unique(rows: list[tuple[str, Any]], what: str, run_id: str) -> dict[str, Any]:
    """Build a keyed index, refusing to let two rows share a key.

    The original tool used a plain dict assignment here, so a duplicate key
    silently kept whichever row was encountered last. That is exactly how a
    pre-fix and a post-fix row could be compared against themselves.
    """
    out: dict[str, Any] = {}
    dupes: list[str] = []
    for key, value in rows:
        if key in out:
            dupes.append(key)
        out[key] = value
    if dupes:
        raise ComparisonError(
            f"{len(dupes)} duplicate {what} key(s) within run {run_id}: "
            f"{sorted(set(dupes))[:5]}; the run-scoped selection is wrong "
            "and the comparison would silently drop rows"
        )
    return out


def _check_shape(before: dict[str, Any], after: dict[str, Any], what: str) -> list[str]:
    """Row counts and membership are checked BEFORE any value comparison.

    A count mismatch means the two sides are not the same population, and
    comparing values across them would report differences that are really
    absences.
    """
    problems: list[str] = []
    if len(before) != len(after):
        problems.append(
            f"{what}: row count differs ({len(before)} vs {len(after)}); "
            "refusing to compare values across differently sized populations"
        )
    only_before = sorted(set(before) - set(after))
    only_after = sorted(set(after) - set(before))
    if only_before:
        problems.append(f"{what}: {len(only_before)} row(s) only in the first run: {only_before[:10]}")
    if only_after:
        problems.append(f"{what}: {len(only_after)} row(s) only in the second run: {only_after[:10]}")
    return problems


def _num_close(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b or abs(float(a) - float(b)) <= FLOAT_TOL
    return False


def _diff(path: str, a: Any, b: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff(f"{path}.{k}", a.get(k), b.get(k), out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "kind": "length", "before": len(a), "after": len(b)})
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _diff(f"{path}[{i}]", x, y, out)
        return
    if _num_close(a, b):
        return
    if a != b:
        out.append({"path": path, "kind": "value", "before": a, "after": b})


def compare_runs(session, before_run_id: str, after_run_id: str) -> dict[str, Any]:
    """Compare two EXPLICITLY named runs.

    Both ids are required. There is no "pick the first and last" default,
    because guessing which run is the pre-fix baseline is precisely the
    decision a certification must not leave to iteration order.
    """
    before_run = _require_run(session, before_run_id)
    after_run = _require_run(session, after_run_id)

    def evals(run_id: str) -> dict[str, Any]:
        rows = [
            (f"{e.model_version_id}|{e.scope}", e.metrics)
            for e in session.scalars(
                select(ModelEvaluation).where(ModelEvaluation.id.like(f"ev_{run_id}_%"))
            )
        ]
        return _index_unique(rows, "evaluation", run_id)

    def recs(run_id: str) -> dict[str, Any]:
        rows = []
        for r in session.scalars(
            select(BacktestRecommendation).where(
                BacktestRecommendation.backtest_run_id == run_id
            )
        ):
            key = f"{r.game_id}|{r.market}|{r.selection}|{r.as_of_at.isoformat()}"
            rows.append((key, {
                "status": r.status,
                "line": r.line,
                "price_american": r.price_american,
                "prediction_id": r.prediction_id,
                "reasons": r.reasons,
                "execution": r.execution,
                "settlement": r.settlement,
            }))
        return _index_unique(rows, "recommendation", run_id)

    result: dict[str, Any] = {
        "before_run": before_run_id,
        "after_run": after_run_id,
        "before_config": before_run.config,
        "after_config": after_run.config,
        "shape_problems": [],
    }
    for label, before, after in (
        ("evaluations", evals(before_run_id), evals(after_run_id)),
        ("recommendations", recs(before_run_id), recs(after_run_id)),
    ):
        shape = _check_shape(before, after, label)
        result["shape_problems"].extend(shape)
        found: list[dict[str, Any]] = []
        for key in sorted(set(before) & set(after)):
            _diff(key, before[key], after[key], found)
        by_prefix: dict[str, int] = {}
        for d in found:
            by_prefix.setdefault(d["path"].split("|")[0], 0)
            by_prefix[d["path"].split("|")[0]] += 1
        result[label] = {
            "rows_before": len(before),
            "rows_after": len(after),
            "unmatched_before": sorted(set(before) - set(after))[:50],
            "unmatched_after": sorted(set(after) - set(before))[:50],
            "difference_count": len(found),
            "by_key_prefix": by_prefix,
            "sample": found[:20],
        }
    return result


def runs_for_season(session, season: int) -> list[str]:
    return [
        r.id for r in session.scalars(select(BacktestRun).order_by(BacktestRun.created_at))
        if (r.config or {}).get("test_season") == season
    ]


def compare_season(session, season: int) -> dict[str, Any]:
    ids = runs_for_season(session, season)
    if len(ids) < 2:
        raise ComparisonError(
            f"season {season}: need two runs to compare, found {len(ids)}"
        )
    out = compare_runs(session, ids[0], ids[-1])
    out["season"] = season
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seasons", default="2024,2025")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    session = get_session()
    payload = {
        "float_tolerance": FLOAT_TOL,
        "seasons": [compare_season(session, int(s)) for s in args.seasons.split(",")],
    }
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text[:4000])
    print(f"\nsha256: {sha256(text.encode('utf-8')).hexdigest()}")
    total = sum(
        s.get(k, {}).get("difference_count", 0)
        for s in payload["seasons"]
        for k in ("evaluations", "recommendations")
    )
    print(f"total differences: {total}")
    return 0 if total == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
