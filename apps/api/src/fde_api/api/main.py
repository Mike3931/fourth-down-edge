"""FastAPI service.

Research only: the API's statuses are RESEARCH_CANDIDATE / WATCH / PASS /
DATA_INCOMPLETE — a production BET state does not exist here. Every
analytical response embeds the research banner and the identifiers
(model version, feature set, calibration, timestamps) needed to
reproduce the number shown.

Long-running work (ingestion, feature builds, backtests, generation)
runs through the job model: POST returns a queued Job immediately;
progress is polled via GET /v1/jobs/{id}.
"""

from __future__ import annotations

import os
import secrets
import threading
import uuid
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from fde_api import RESEARCH_BANNER, __version__
from fde_api.api import schemas
from fde_api.api.schemas import ApprovalStatus, JobStatus
from fde_api.db.engine import get_session, session_scope
from fde_api.db.models import (
    BacktestRun,
    CalibrationArtifact,
    Game,
    Job,
    ModelEvaluation,
    ModelVersion,
    Prediction,
)
from fde_api.util import utc_now

app = FastAPI(
    title="Fourth Down Edge — Analytical Engine",
    version=__version__,
    description=RESEARCH_BANNER,
)

# The web app is a separate origin (Vite dev server / GitHub Pages), so the
# browser needs explicit CORS permission or every research fetch fails and the
# UI shows DATA INCOMPLETE forever. Origins are configurable and default to the
# local dev server only — this service is not intended to be public.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get(
        "FDE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# --------------------------------------------------------------------------- #
# Auth: bearer token via FDE_API_TOKEN. Serving without one requires
# FDE_ALLOW_UNAUTHENTICATED=1 — an unset token is a misconfiguration, not
# an invitation.
# --------------------------------------------------------------------------- #


def auth_state() -> Literal["enabled", "disabled", "misconfigured"]:
    """Whether the API is protected, and if not, whether that was on purpose.

    An unset token used to mean "local dev, serve everything". That fails
    OPEN: a deployment where the environment simply failed to load would
    serve every endpoint unauthenticated and report itself healthy. This
    mirrors the discipline already applied to the odds credential, which
    fails readiness checks when absent rather than degrading quietly.

    Running without a token now requires saying so explicitly.
    """
    if os.environ.get("FDE_API_TOKEN"):
        return "enabled"
    if os.environ.get("FDE_ALLOW_UNAUTHENTICATED") == "1":
        return "disabled"
    return "misconfigured"


def _require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    state = auth_state()
    if state == "disabled":
        return  # explicitly opted out; surfaced in /health
    if state == "misconfigured":
        raise HTTPException(
            status_code=503,
            detail=(
                "API token not configured. Set FDE_API_TOKEN, or set "
                "FDE_ALLOW_UNAUTHENTICATED=1 to serve without authentication "
                "on purpose."
            ),
        )
    token = os.environ["FDE_API_TOKEN"]
    # compare_digest, not ==: string equality short-circuits on the first
    # differing byte, which leaks the token one character at a time to
    # anyone who can time the responses.
    expected = f"Bearer {token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


Auth = Depends(_require_auth)

# /health must distinguish "deliberately open" from "nobody configured a
# token". Reporting both as "disabled (local dev)" is how an unprotected
# deployment reads as a normal one.
_AUTH_LABELS: dict[str, schemas.AuthState] = {
    "enabled": "enabled",
    "disabled": "disabled (explicitly allowed)",
    "misconfigured": "MISCONFIGURED — no token set and unauthenticated access not allowed",
}


# --------------------------------------------------------------------------- #
# Job model
# --------------------------------------------------------------------------- #


def _start_job(kind: str, params: dict[str, Any], work) -> str:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    with session_scope() as s:
        s.add(Job(id=job_id, kind=kind, params=params, status="queued", created_at=utc_now()))

    def runner() -> None:
        with session_scope() as s:
            running = s.get(Job, job_id)
            if running is not None:
                running.status = "running"
                running.started_at = utc_now()
        try:
            result = work()
            with session_scope() as s:
                done = s.get(Job, job_id)
                if done is not None:
                    done.status = "finished"
                    done.result = result
                    done.finished_at = utc_now()
        except Exception as e:
            with session_scope() as s:
                failed = s.get(Job, job_id)
                if failed is not None:
                    failed.status = "failed"
                    failed.error = f"{type(e).__name__}: {e}"
                    failed.finished_at = utc_now()

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def _load_job(job_id: str) -> Job:
    job = get_session().get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job {job_id}")
    return job


def _job_response(job: Job) -> schemas.JobResponse:
    return schemas.JobResponse(
        id=job.id, kind=job.kind, status=cast(JobStatus, job.status), params=job.params, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at,
    )


# --------------------------------------------------------------------------- #
# Health & registry
# --------------------------------------------------------------------------- #


@app.get("/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    try:
        s = get_session()
        games = s.scalar(select(func.count(Game.id))) or 0
        preds = s.scalar(select(func.count(Prediction.id))) or 0
        db = "ok"
    except Exception:
        games = preds = 0
        db = "unavailable"
    return schemas.HealthResponse(
        status="ok" if db == "ok" else "degraded",
        research_banner=RESEARCH_BANNER,
        database=cast(Literal["ok", "unavailable"], db),
        games=games,
        predictions=preds,
        auth=_AUTH_LABELS[auth_state()],
        version=__version__,
    )


@app.get("/v1/models", response_model=list[schemas.ModelSummary], dependencies=[Auth])
def list_models() -> list[schemas.ModelSummary]:
    s = get_session()
    return [
        schemas.ModelSummary(
            id=m.id, name=m.name, target=m.target, algorithm=m.algorithm,
            approval_status=cast(ApprovalStatus, m.approval_status), feature_set=m.feature_set,
            calibration_version=m.calibration_version, train_window=m.train_window,
            validation_window=m.validation_window, test_window=m.test_window, created_at=m.created_at,
        )
        for m in s.scalars(select(ModelVersion).order_by(ModelVersion.created_at))
    ]


@app.get("/v1/models/{model_version}", response_model=schemas.ModelDetail, dependencies=[Auth])
def get_model(model_version: str) -> schemas.ModelDetail:
    s = get_session()
    m = s.get(ModelVersion, model_version)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Unknown model version {model_version}")
    return schemas.ModelDetail(
        id=m.id, name=m.name, target=m.target, algorithm=m.algorithm,
        approval_status=cast(ApprovalStatus, m.approval_status), feature_set=m.feature_set,
        calibration_version=m.calibration_version, train_window=m.train_window,
        validation_window=m.validation_window, test_window=m.test_window, created_at=m.created_at,
        hyperparameters=m.hyperparameters, random_seed=m.random_seed, dataset_hashes=m.dataset_hashes,
        artifact_hash=m.artifact_hash, code_commit=m.code_commit,
        dependency_lock_hash=m.dependency_lock_hash, metrics=m.metrics,
        known_limitations=m.known_limitations,
    )


# --------------------------------------------------------------------------- #
# Jobs: ingestion, features, backtests, prediction generation
# --------------------------------------------------------------------------- #


@app.post("/v1/data/ingest/nflverse", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def ingest_nflverse_endpoint(req: schemas.IngestRequest) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.ingest import ingest_nflverse

        with session_scope() as s:
            report = ingest_nflverse(s, req.seasons)
        return {"loads": report.loads, "errors": report.errors,
                "manifests": [m.version_id for m in report.manifests]}

    return _job_response(_load_job(_start_job("ingest_nflverse", req.model_dump(), work)))


@app.post("/v1/features/build", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def build_features_endpoint(req: schemas.FeaturesBuildRequest) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.backtest.walkforward import build_or_load_features
        from fde_api.features import LeagueHistory
        from fde_api.pit.clock import PredictionHorizon

        with session_scope() as s:
            history = LeagueHistory.load(s)
        feats = build_or_load_features(history, req.half_life_days, PredictionHorizon(req.horizon))
        return {"games_with_features": len(feats), "half_life_days": req.half_life_days,
                "horizon": req.horizon, "feature_set": "nfl-core-v1"}

    return _job_response(_load_job(_start_job("features_build", req.model_dump(), work)))


@app.post("/v1/backtests/run", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def run_backtest_endpoint(req: schemas.BacktestRequest) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.backtest.walkforward import WalkForwardConfig, run_walkforward

        with session_scope() as s:
            res = run_walkforward(s, WalkForwardConfig(test_season=req.test_season))
        return {"run_id": res["run_id"], "fold": res["fold"]}

    return _job_response(_load_job(_start_job("backtest_run", req.model_dump(), work)))


@app.get("/v1/jobs/{job_id}", response_model=schemas.JobResponse, dependencies=[Auth])
def get_job(job_id: str) -> schemas.JobResponse:
    return _job_response(_load_job(job_id))


@app.get("/v1/backtests/{run_id}", response_model=schemas.BacktestRunResponse, dependencies=[Auth])
def get_backtest(run_id: str) -> schemas.BacktestRunResponse:
    s = get_session()
    run = s.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown backtest run {run_id}")
    return schemas.BacktestRunResponse(
        id=run.id, status=run.status, config=run.config, error=run.error,
        created_at=run.created_at, finished_at=run.finished_at,
    )


@app.get("/v1/backtests/{run_id}/metrics", response_model=schemas.BacktestMetricsResponse, dependencies=[Auth])
def get_backtest_metrics(run_id: str) -> schemas.BacktestMetricsResponse:
    s = get_session()
    run = s.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown backtest run {run_id}")
    if run.status != "finished" or run.metrics is None:
        raise HTTPException(status_code=409, detail=f"Backtest {run_id} is {run.status}; metrics not available")
    return schemas.BacktestMetricsResponse(run_id=run.id, metrics=run.metrics, research_banner=RESEARCH_BANNER)


@app.post("/v1/predictions/generate", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def generate_predictions(req: schemas.GeneratePredictionsRequest) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.services.generate import generate_prediction

        with session_scope() as s:
            pred_id = generate_prediction(s, req.game_id, req.model_version_id, req.horizon)
        return {"prediction_id": pred_id}

    return _job_response(_load_job(_start_job("predictions_generate", req.model_dump(), work)))


# --------------------------------------------------------------------------- #
# Predictions & performance
# --------------------------------------------------------------------------- #


def _prediction_response(s, p: Prediction) -> schemas.PredictionResponse:
    mv = s.get(ModelVersion, p.model_version_id)
    return schemas.PredictionResponse(
        id=p.id, game_id=p.game_id, model_version_id=p.model_version_id,
        model_approval_status=cast(ApprovalStatus, mv.approval_status) if mv else "research_only",
        feature_snapshot_id=p.feature_snapshot_id, horizon=p.horizon, as_of_at=p.as_of_at,
        created_at=p.created_at,
        outputs=schemas.PredictionOutputs(**p.outputs),
        research_banner=RESEARCH_BANNER,
    )


@app.get("/v1/predictions/{prediction_id}", response_model=schemas.PredictionResponse, dependencies=[Auth])
def get_prediction(prediction_id: str) -> schemas.PredictionResponse:
    s = get_session()
    p = s.get(Prediction, prediction_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Unknown prediction {prediction_id}")
    return _prediction_response(s, p)


@app.get("/v1/games/{game_id}/predictions", response_model=list[schemas.PredictionResponse], dependencies=[Auth])
def get_game_predictions(game_id: str) -> list[schemas.PredictionResponse]:
    s = get_session()
    if s.get(Game, game_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown game {game_id}")
    preds = s.scalars(
        select(Prediction).where(Prediction.game_id == game_id).order_by(Prediction.as_of_at)
    ).all()
    return [_prediction_response(s, p) for p in preds]


@app.get("/v1/performance/calibration", response_model=schemas.CalibrationReportResponse, dependencies=[Auth])
def get_calibration() -> schemas.CalibrationReportResponse:
    s = get_session()
    artifacts = [
        {
            "id": a.id, "method": a.method, "target": a.target, "fitted_on": a.fitted_on,
            "parameters": a.parameters, "sample_size": a.sample_size,
            "created_at": a.created_at.isoformat(),
        }
        for a in s.scalars(select(CalibrationArtifact).order_by(CalibrationArtifact.created_at))
    ]
    return schemas.CalibrationReportResponse(artifacts=artifacts, research_banner=RESEARCH_BANNER)


@app.get("/v1/performance/model-comparison", response_model=schemas.ModelComparisonResponse, dependencies=[Auth])
def get_model_comparison() -> schemas.ModelComparisonResponse:
    s = get_session()
    rows = [
        schemas.ModelComparisonRow(
            model_version_id=e.model_version_id, scope=e.scope, sample_size=e.sample_size, metrics=e.metrics
        )
        for e in s.scalars(select(ModelEvaluation).order_by(ModelEvaluation.scope, ModelEvaluation.model_version_id))
    ]
    return schemas.ModelComparisonResponse(
        rows=rows, market_benchmark_id="market-benchmark-v1", research_banner=RESEARCH_BANNER
    )
