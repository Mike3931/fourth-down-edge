"""Pre-game attestation and fail-closed graph verification; no model replay."""

import hashlib
from datetime import datetime

from fde_api.config import settings
from fde_api.db.models import FeatureSnapshot, ForecastFreeze, Game, ModelVersion
from fde_api.forward.intelligence import digest


def graph_hash(prediction, feature, model):
    def row(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return digest([row(prediction), row(feature), row(model)])


def freeze_forecast(s, prediction, *, now):
    feature = s.get(FeatureSnapshot, prediction.feature_snapshot_id)
    model = s.get(ModelVersion, prediction.model_version_id)
    if now >= datetime.fromisoformat(feature.values["kickoff_at_creation"]):
        raise ValueError("Cannot freeze a prediction after kickoff")
    s.add(
        ForecastFreeze(
            prediction_id=prediction.id,
            graph_hash=graph_hash(prediction, feature, model),
            attested_at=now,
            recorded_at=now,
            provenance="atomic pregame publication",
        )
    )


def eligibility(s, p, game):
    """Return a human-readable blocker, or None. Historical records without proof stay blocked."""
    f = s.get(FeatureSnapshot, p.feature_snapshot_id) if p.feature_snapshot_id else None
    m = s.get(ModelVersion, p.model_version_id)
    seal = s.get(ForecastFreeze, p.id)
    if not f or not m:
        return "Missing frozen inputs or model metadata"
    if not seal:
        return "Missing pregame freeze attestation"
    if seal.graph_hash != graph_hash(p, f, m):
        return "Frozen forecast, inputs or model hash mismatch"
    try:
        kickoff = datetime.fromisoformat(f.values["kickoff_at_creation"])
        if (
            not game
            or not game.kickoff_utc
            or p.created_at != p.as_of_at
            or f.created_at != p.created_at
            or f.as_of_at != p.as_of_at
            or p.created_at >= min(kickoff, game.kickoff_utc)
            or seal.attested_at < p.created_at
            or seal.attested_at >= min(kickoff, game.kickoff_utc)
        ):
            return "Forecast or freeze is outside the pregame cutoff"
        canonical = s.get(Game, p.game_id)
        if (
            f.game_id != p.game_id
            or f.horizon != p.horizon
            or not canonical
            or (canonical.home_team_id, canonical.away_team_id) != (game.home_team_id, game.away_team_id)
        ):
            return "Game or home/away identity mismatch"
        if m.approval_status != "research_only" or not p.outputs.get("research_only"):
            return "Model is outside the research-only cohort"
        path = settings.artifacts_dir / f"model_{m.name}_{m.artifact_hash[:12]}.pkl"
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != m.artifact_hash:
            return "Model artifact missing or hash mismatch"
    except (KeyError, TypeError, ValueError):
        return "Incomplete forecast integrity metadata"
    return None
