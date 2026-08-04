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


def compare_season(session, season: int) -> dict[str, Any]:
    runs = [
        r for r in session.scalars(select(BacktestRun).order_by(BacktestRun.created_at))
        if (r.config or {}).get("test_season") == season
    ]
    if len(runs) < 2:
        return {"season": season, "error": f"need 2 runs, found {len(runs)}"}
    original, rerun = runs[0], runs[-1]

    def evals(run_id: str) -> dict[str, Any]:
        return {
            f"{e.model_version_id}|{e.scope}": e.metrics
            for e in session.scalars(
                select(ModelEvaluation).where(ModelEvaluation.id.like(f"ev_{run_id}_%"))
            )
        }

    def recs(run_id: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for r in session.scalars(
            select(BacktestRecommendation).where(
                BacktestRecommendation.backtest_run_id == run_id
            )
        ):
            key = f"{r.game_id}|{r.market}|{r.selection}|{r.as_of_at.isoformat()}"
            out[key] = {
                "status": r.status,
                "line": r.line,
                "price_american": r.price_american,
                "reasons": r.reasons,
                "execution": r.execution,
                "settlement": r.settlement,
            }
        return out

    result: dict[str, Any] = {
        "season": season,
        "original_run": original.id,
        "rerun": rerun.id,
    }
    for label, before, after in (
        ("evaluations", evals(original.id), evals(rerun.id)),
        ("recommendations", recs(original.id), recs(rerun.id)),
    ):
        found: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            if key not in before:
                found.append({"path": key, "kind": "added"})
            elif key not in after:
                found.append({"path": key, "kind": "removed"})
            else:
                _diff(key, before[key], after[key], found)
        by_model: dict[str, int] = {}
        for d in found:
            model = d["path"].split("|")[0] if "|" in d["path"] else "—"
            by_model[model] = by_model.get(model, 0) + 1
        result[label] = {
            "rows_before": len(before),
            "rows_after": len(after),
            "difference_count": len(found),
            "by_key_prefix": by_model,
            "sample": found[:20],
        }
    return result


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
