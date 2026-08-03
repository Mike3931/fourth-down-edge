"""Typed API contracts (Pydantic v2).

Every payload that carries an analytical number also carries the model
version, feature set, calibration version, and timestamps needed to
reproduce it. Research status is explicit and non-optional.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ResearchStatus = Literal["RESEARCH_CANDIDATE", "WATCH", "PASS", "DATA_INCOMPLETE"]
ApprovalStatus = Literal["research_only", "approved", "retired"]
JobStatus = Literal["queued", "running", "finished", "failed"]


# Three states, not two. "Nobody configured a token" is not the same
# condition as "running open on purpose", and collapsing them is how an
# unprotected deployment reads as a normal one.
AuthState = Literal[
    "enabled",
    "disabled (explicitly allowed)",
    "MISCONFIGURED — no token set and unauthenticated access not allowed",
]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    research_banner: str
    database: Literal["ok", "unavailable"]
    games: int
    predictions: int
    auth: AuthState
    version: str


class ModelSummary(BaseModel):
    id: str
    name: str
    target: str
    algorithm: str
    approval_status: ApprovalStatus
    feature_set: str | None
    calibration_version: str | None
    train_window: str | None
    validation_window: str | None
    test_window: str | None
    created_at: datetime


class ModelDetail(ModelSummary):
    hyperparameters: dict[str, Any]
    random_seed: int | None
    dataset_hashes: dict[str, Any]
    artifact_hash: str | None
    code_commit: str
    dependency_lock_hash: str
    metrics: dict[str, Any]
    known_limitations: str


class JobResponse(BaseModel):
    id: str
    kind: str
    status: JobStatus
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IngestRequest(BaseModel):
    seasons: list[int] = Field(min_length=1, max_length=30)


class FeaturesBuildRequest(BaseModel):
    half_life_days: float = Field(default=365.0, gt=0)
    horizon: str = "PREGAME"


class BacktestRequest(BaseModel):
    test_season: int = Field(ge=2000, le=2100)


class BacktestRunResponse(BaseModel):
    id: str
    status: str
    config: dict[str, Any]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class BacktestMetricsResponse(BaseModel):
    run_id: str
    metrics: dict[str, Any]
    research_banner: str


class PredictionOutputs(BaseModel):
    expected_home_points: float
    expected_away_points: float
    expected_margin: float
    expected_total: float
    sigma_margin: float
    sigma_total: float
    home_win_prob: float
    spread_cover_prob: float | None = None
    spread_push_prob: float | None = None
    total_over_prob: float | None = None
    total_push_prob: float | None = None
    margin_p10: float
    margin_p90: float
    total_p10: float
    total_p90: float
    components: dict[str, Any] | None = None


class PredictionResponse(BaseModel):
    id: str
    game_id: str
    model_version_id: str
    model_approval_status: ApprovalStatus
    feature_snapshot_id: str | None
    horizon: str
    as_of_at: datetime
    created_at: datetime
    outputs: PredictionOutputs
    research_banner: str


class GeneratePredictionsRequest(BaseModel):
    game_id: str
    model_version_id: str
    horizon: str = "PREGAME"


class CalibrationReportResponse(BaseModel):
    artifacts: list[dict[str, Any]]
    research_banner: str


class ModelComparisonRow(BaseModel):
    model_version_id: str
    scope: str
    sample_size: int
    metrics: dict[str, Any]


class ModelComparisonResponse(BaseModel):
    rows: list[ModelComparisonRow]
    market_benchmark_id: str
    research_banner: str


class ApiError(BaseModel):
    detail: str
