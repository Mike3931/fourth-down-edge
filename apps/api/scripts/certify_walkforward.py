"""Run the walk-forward integrity certification end to end.

Snapshots the artifacts already in the database (produced by the pre-fix
code, i.e. the published Phase 2 evaluation), reruns every recorded
walk-forward from its OWN stored config, snapshots again, and diffs.

No new selection and no tuning: each rerun reuses the exact config the
original run recorded, so seasons, folds, grids, seeds and execution
assumptions are identical by construction rather than by transcription.

    python scripts/certify_walkforward.py --out ../../reports/integrity
"""

from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select

from fde_api.backtest.certification import compare, snapshot_hash, take_snapshot
from fde_api.backtest.execution import ExecutionConfig
from fde_api.backtest.walkforward import WalkForwardConfig, run_walkforward
from fde_api.db.engine import get_session, session_scope
from fde_api.db.models import BacktestRun
from fde_api.pit.clock import PredictionHorizon


def _config_from_stored(cfg: dict[str, Any]) -> WalkForwardConfig:
    """Rebuild the exact config a previous run recorded."""
    ex = cfg.get("execution") or {}
    return WalkForwardConfig(
        test_season=int(cfg["test_season"]),
        horizon=PredictionHorizon(cfg.get("horizon", "PREGAME")),
        half_life_grid=tuple(cfg["half_life_grid"]),
        ridge_alpha_grid=tuple(cfg["ridge_alpha_grid"]),
        ratings_k_grid=tuple(cfg["ratings_k_grid"]),
        candidate_edge_grid=tuple(cfg["candidate_edge_grid"]),
        watch_margin=float(cfg.get("watch_margin", 0.015)),
        seed=int(cfg["seed"]),
        execution=ExecutionConfig(
            price_deterioration_cents=int(ex.get("price_deterioration_cents", 5)),
            halfpoint_deterioration_prob=float(ex.get("halfpoint_deterioration_prob", 0.25)),
            no_fill_prob=float(ex.get("no_fill_prob", 0.02)),
            suspended_prob=float(ex.get("suspended_prob", 0.005)),
            manual_delay_minutes=float(ex.get("manual_delay_minutes", 3.0)),
            seed=int(ex.get("seed", 20260801)),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory for the reports")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    session = get_session()

    before = take_snapshot(session)
    before_hash = snapshot_hash(before)
    print(f"pre-fix snapshot : {before.counts()}")
    print(f"pre-fix hash     : {before_hash}")

    # One rerun per DISTINCT config, not per historical run.
    #
    # Every certification appends the runs it performs, and those become
    # inputs to the next certification. Replaying per run rather than per
    # config therefore doubles the work each time: two seasons became four
    # runs after the first certification, and would have become eight
    # here. The reruns beyond the first per config are exact repeats -
    # same season, same grids, same seeds - so they add cost and rows
    # without adding evidence.
    #
    # The earliest run carrying each config is the one replayed, which
    # keeps the choice deterministic rather than dependent on how many
    # certifications have happened.
    stored = []
    seen_configs: set[str] = set()
    for r in session.scalars(select(BacktestRun).order_by(BacktestRun.created_at)):
        if not (r.config and r.config.get("test_season")):
            continue
        key = json.dumps(dict(r.config), sort_keys=True, default=str)
        if key in seen_configs:
            print(f"  skipping {r.id}: config identical to an earlier run")
            continue
        seen_configs.add(key)
        stored.append((r.id, dict(r.config)))
    if not stored:
        print("no recorded walk-forward runs to certify against", file=sys.stderr)
        return 1
    print(f"reruns to perform: {[c['test_season'] for _, c in stored]}")

    for run_id, cfg in stored:
        season = cfg["test_season"]
        print(f"  rerunning test_season={season} (original run {run_id}) ...", flush=True)
        with session_scope() as s:
            run_walkforward(s, _config_from_stored(cfg))

    session.expire_all()
    after = take_snapshot(session)
    after_hash = snapshot_hash(after)
    print(f"post-fix snapshot: {after.counts()}")
    print(f"post-fix hash    : {after_hash}")

    result = compare(before, after)
    result["pre_fix_snapshot_sha256"] = before_hash
    result["post_fix_snapshot_sha256"] = after_hash
    result["reran_test_seasons"] = [c["test_season"] for _, c in stored]

    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    (out / "walkforward-prefix-vs-postfix.json").write_text(text, encoding="utf-8")
    digest = sha256(text.encode("utf-8")).hexdigest()

    print()
    print(f"differences found: {result['difference_count']}")
    print(f"identical        : {result['identical']}")
    print(f"comparison sha256: {digest}")
    if not result["identical"]:
        print("\nfirst differences:")
        for section, items in result["differences"].items():
            for d in items[:10]:
                print(f"  [{section}] {d}")
    return 0 if result["identical"] else 2


if __name__ == "__main__":
    sys.exit(main())
