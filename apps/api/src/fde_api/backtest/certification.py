"""Pre-fix versus post-fix comparison for the walk-forward integrity gate.

The replay hardening changed how a sequential model observes results. The
question this answers is not "do the tests pass" but "did any previously
reported historical number move".

Method:

  1. SNAPSHOT the artifacts already in the database. Those were produced by
     the pre-fix code and are the published Phase 2 evaluation.
  2. RERUN every recorded walk-forward from scratch, reusing each run's own
     stored config verbatim - same seasons, folds, grids, seeds, execution
     assumptions. No new selection, no tuning.
  3. SNAPSHOT again and diff.

Everything is compared, not sampled: fold membership, selected
hyperparameters, calibration choice, every test-game prediction, every
candidate status, and every metric.

Exact equality is required for identifiers, statuses, and selections.
Floating-point values are compared with an explicit tolerance because the
rerun happens in a fresh process and numpy reductions are not guaranteed
bit-identical across runs; any value that moves by more than the tolerance
is reported individually rather than summarised away.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.models import (
    BacktestRecommendation,
    BacktestRun,
    CalibrationArtifact,
    ModelEvaluation,
    ModelVersion,
    Prediction,
)

# Tolerance for floating-point comparison. Tight enough that a real change
# in model behaviour cannot hide under it - a different observation order
# or an extra observed game moves ratings by orders of magnitude more.
FLOAT_TOL = 1e-9


@dataclass
class Snapshot:
    predictions: dict[str, dict[str, Any]] = field(default_factory=dict)
    evaluations: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommendations: dict[str, dict[str, Any]] = field(default_factory=dict)
    calibrations: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "predictions": len(self.predictions),
            "evaluations": len(self.evaluations),
            "recommendations": len(self.recommendations),
            "calibrations": len(self.calibrations),
            "models": len(self.models),
            "runs": len(self.runs),
        }


def take_snapshot(session: Session) -> Snapshot:
    s = Snapshot()
    for p in session.scalars(select(Prediction)):
        s.predictions[p.id] = {
            "game_id": p.game_id,
            "model_version_id": p.model_version_id,
            "horizon": p.horizon,
            "as_of_at": p.as_of_at.isoformat() if p.as_of_at else None,
            "outputs": p.outputs,
        }
    for e in session.scalars(select(ModelEvaluation)):
        s.evaluations[f"{e.model_version_id}|{e.scope}"] = {"metrics": e.metrics}
    # Keyed on a STABLE composite, not the autoincrement primary key: a
    # rerun mints new ids, so keying on them would report every row as
    # added-and-removed and prove nothing.
    for r in session.scalars(select(BacktestRecommendation)):
        key = f"{r.game_id}|{r.market}|{r.selection}|{r.as_of_at.isoformat()}"
        s.recommendations[key] = {
            "status": r.status,
            "line": r.line,
            "price_american": r.price_american,
            "reasons": r.reasons,
            "execution": r.execution,
            "settlement": r.settlement,
            "prediction_id": r.prediction_id,
        }
    for c in session.scalars(select(CalibrationArtifact)):
        s.calibrations[c.id] = {
            "method": c.method,
            "fitted_on": c.fitted_on,
            "parameters": c.parameters,
            "sample_size": c.sample_size,
        }
    for m in session.scalars(select(ModelVersion)):
        s.models[m.id] = {
            "train_window": m.train_window,
            "validation_window": m.validation_window,
            "test_window": m.test_window,
            "feature_set": m.feature_set,
            "calibration_version": m.calibration_version,
            "approval_status": m.approval_status,
        }
    # Runs are keyed by the test season they cover, not their generated id.
    # A rerun creates a new row; what must match is the fold definition and
    # the metrics, not the uuid.
    for run in session.scalars(select(BacktestRun)):
        cfg = run.config or {}
        season = cfg.get("test_season")
        key = f"test_season={season}"
        prior = s.runs.get(key)
        # Keep the most recently created row per season.
        if prior is None or (run.created_at and prior.get("_created", "") <= run.created_at.isoformat()):
            s.runs[key] = {
                "config": cfg,
                "status": run.status,
                "metrics": run.metrics,
                "_created": run.created_at.isoformat() if run.created_at else "",
            }
    for v in s.runs.values():
        v.pop("_created", None)
    return s


def _diff_value(path: str, a: Any, b: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff_value(f"{path}.{k}", a.get(k), b.get(k), out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "kind": "length", "before": len(a), "after": len(b)})
            return
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _diff_value(f"{path}[{i}]", x, y, out)
        return
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        if a != b and abs(float(a) - float(b)) > FLOAT_TOL:
            out.append({"path": path, "kind": "numeric", "before": a, "after": b,
                        "delta": float(b) - float(a)})
        return
    if a != b:
        out.append({"path": path, "kind": "exact", "before": a, "after": b})


def compare(before: Snapshot, after: Snapshot) -> dict[str, Any]:
    diffs: dict[str, list[dict[str, Any]]] = {}
    for name in ("predictions", "evaluations", "recommendations", "calibrations", "models", "runs"):
        b: dict[str, Any] = getattr(before, name)
        a: dict[str, Any] = getattr(after, name)
        found: list[dict[str, Any]] = []
        for key in sorted(set(b) | set(a)):
            if key not in b:
                found.append({"path": key, "kind": "added"})
            elif key not in a:
                found.append({"path": key, "kind": "removed"})
            else:
                _diff_value(key, b[key], a[key], found)
        diffs[name] = found

    total = sum(len(v) for v in diffs.values())
    return {
        "float_tolerance": FLOAT_TOL,
        "counts_before": before.counts(),
        "counts_after": after.counts(),
        "difference_count": total,
        "identical": total == 0,
        # Cap the listing so a catastrophic mismatch does not produce an
        # unreadable report; the count above is the authoritative figure.
        "differences": {k: v[:50] for k, v in diffs.items() if v},
    }


def snapshot_hash(s: Snapshot) -> str:
    payload = json.dumps(
        {
            "predictions": s.predictions,
            "evaluations": s.evaluations,
            "recommendations": s.recommendations,
            "calibrations": s.calibrations,
            "models": s.models,
        },
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()
