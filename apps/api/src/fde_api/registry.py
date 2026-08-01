"""Model registry.

Every model artifact is registered with full audit metadata before it
may store predictions (the predictions table enforces this by foreign
key). Approval status starts — and in this phase stays — research_only;
the API refuses to serve predictions from any other status than an
explicitly approved artifact, and nothing in this phase performs
approval.

The metadata layout mirrors MLflow's run/model schema (params, metrics,
tags) so runs can be exported to an MLflow tracking store without
translation.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from typing import Any

from sqlalchemy.orm import Session

from fde_api.config import settings
from fde_api.db.models import ModelVersion
from fde_api.models_ml.protocol import GamePredictor
from fde_api.util import current_code_commit, dependency_lock_hash, utc_now

RESEARCH_ONLY = "research_only"


def register_model(
    session: Session,
    model: GamePredictor,
    *,
    target: str,
    train_window: str,
    validation_window: str,
    test_window: str,
    feature_set: str | None,
    calibration_version: str | None,
    dataset_hashes: dict[str, str],
    metrics: dict[str, Any],
    known_limitations: str,
    random_seed: int | None,
) -> ModelVersion:
    desc = model.describe()
    artifact_bytes = pickle.dumps(model)
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    settings.ensure_dirs()
    artifact_path = settings.artifacts_dir / f"model_{model.name}_{artifact_hash[:12]}.pkl"
    if not artifact_path.exists():
        artifact_path.write_bytes(artifact_bytes)

    # MLflow-compatible sidecar: params/metrics/tags layout.
    mlmeta = {
        "run_name": model.name,
        "params": {k: v for k, v in desc.items() if k != "name"},
        "metrics": {k: v for k, v in metrics.items() if isinstance(v, int | float)},
        "tags": {
            "approval_status": RESEARCH_ONLY,
            "code_commit": current_code_commit(),
            "feature_set": feature_set or "",
        },
    }
    (settings.artifacts_dir / f"model_{model.name}_{artifact_hash[:12]}.mlmeta.json").write_text(
        json.dumps(mlmeta, indent=2, default=str), encoding="utf-8"
    )

    mv = ModelVersion(
        id=model.name,
        name=model.name,
        target=target,
        algorithm=desc.get("update", desc.get("algorithm", type(model).__name__)),
        train_window=train_window,
        validation_window=validation_window,
        test_window=test_window,
        feature_set=feature_set,
        calibration_version=calibration_version,
        hyperparameters={k: v for k, v in desc.items() if k not in ("name", "features")},
        random_seed=random_seed,
        dataset_hashes=dataset_hashes,
        artifact_hash=artifact_hash,
        code_commit=current_code_commit(),
        dependency_lock_hash=dependency_lock_hash(),
        metrics={k: v for k, v in metrics.items() if k != "reliability"},
        known_limitations=known_limitations,
        approval_status=RESEARCH_ONLY,
        created_at=utc_now(),
    )
    session.merge(mv)
    session.flush()
    return mv


def load_model_artifact(artifact_hash: str, name: str) -> GamePredictor:
    path = settings.artifacts_dir / f"model_{name}_{artifact_hash[:12]}.pkl"
    obj = pickle.loads(path.read_bytes())
    if not isinstance(obj, GamePredictor):
        raise TypeError(f"artifact {path} is not a GamePredictor")
    return obj
