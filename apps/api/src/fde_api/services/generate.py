"""On-demand prediction generation from a registered artifact.

Serving rules enforced here:
  * The model version must exist in the registry.
  * research_only artifacts ARE servable — as research output with the
    banner attached (the API schema carries approval status); an
    artifact with any unknown status is refused.
  * The feature snapshot is built at the requested horizon's cutoff and
    persisted, so the prediction row links to reproducible inputs.
  * Prediction rows are immutable: regenerating an existing vintage must
    produce identical content (deterministic models); a conflicting
    regeneration is refused rather than overwritten.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fde_api.db.models import Game, ModelVersion, Prediction
from fde_api.features import CoreV1Builder, LeagueHistory
from fde_api.pit.clock import PredictionHorizon, horizon_as_of
from fde_api.registry import load_model_artifact
from fde_api.util import utc_now


def generate_prediction(session: Session, game_id: str, model_version_id: str, horizon: str) -> str:
    mv = session.get(ModelVersion, model_version_id)
    if mv is None:
        raise LookupError(f"Model version {model_version_id} is not registered")
    if mv.approval_status not in ("research_only", "approved"):
        raise PermissionError(f"Model {model_version_id} has status {mv.approval_status}; refusing to serve")
    game_row = session.get(Game, game_id)
    if game_row is None:
        raise LookupError(f"Unknown game {game_id}")
    h = PredictionHorizon(horizon)

    history = LeagueHistory.load(session)
    game = history.by_id.get(game_id)
    if game is None:
        raise LookupError(f"Game {game_id} has no replayable kickoff")

    builder = CoreV1Builder(history)
    snap = builder.build(game, h, session)

    model = load_model_artifact(mv.artifact_hash or "", mv.name)
    market = None
    if model.uses_market:
        from fde_api.backtest.walkforward import load_market_refs

        refs, _ = load_market_refs(session)
        market = refs.get(game_id)
        if market is None:
            raise LookupError(f"{mv.name} requires a market reference; none exists for {game_id}")
    pm = model.predict(game, snap.values, market)
    outputs = pm.distribution().outputs(
        market.home_line if market else None, market.total_line if market else None
    ) | {"components": pm.components}

    as_of = horizon_as_of(game.kickoff, h)
    pred_id = f"pred_{game_id}_{mv.id}_{h.value}"
    existing = session.get(Prediction, pred_id)
    if existing is not None:
        if existing.outputs != outputs:
            raise RuntimeError(
                f"Prediction {pred_id} already exists with different content; vintages are immutable"
            )
        return pred_id
    session.add(
        Prediction(
            id=pred_id,
            game_id=game_id,
            model_version_id=mv.id,
            feature_snapshot_id=snap.id,
            horizon=h.value,
            as_of_at=as_of,
            outputs=outputs,
            created_at=utc_now(),
        )
    )
    session.flush()
    return pred_id
