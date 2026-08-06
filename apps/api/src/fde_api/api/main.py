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


# --------------------------------------------------------------------------- #
# Live forward slate
# --------------------------------------------------------------------------- #
#
# Reads what has actually been captured: real fixtures, real book prices,
# and the consensus builder's own verdict. It computes nothing itself and
# fills nothing in. A market with too few books returns a null consensus
# and the reason, because "we could not price this" is the answer, and
# rendering an empty slot as a number is how a screen starts lying.


@app.get("/v1/forward/live", dependencies=[Auth])
def get_forward_live(data_mode: str = "LIVE_RESEARCH", hours: int = 72) -> dict[str, Any]:
    from datetime import timedelta

    from fde_api.db.forward_models import OddsQuote, ScheduleObservation
    from fde_api.forward.consensus import build_consensus
    from fde_api.forward.modes import DataMode

    now = utc_now()
    mode = DataMode(data_mode)
    horizon = now + timedelta(hours=hours)

    with session_scope() as s:
        observations = list(
            s.scalars(
                select(ScheduleObservation).where(
                    ScheduleObservation.data_mode == mode.value,
                    ScheduleObservation.kickoff_utc >= now - timedelta(hours=6),
                    ScheduleObservation.kickoff_utc <= horizon,
                ).order_by(ScheduleObservation.kickoff_utc)
            )
        )
        # One entry per game: the schedule is append-only, so the newest
        # observation of a fixture is the current one.
        latest: dict[str, Any] = {}
        for o in observations:
            latest[o.canonical_game_id] = o

        games: list[dict[str, Any]] = []
        for gid, o in sorted(latest.items(), key=lambda kv: kv[1].kickoff_utc):
            quotes = list(
                s.scalars(
                    select(OddsQuote).where(
                        OddsQuote.canonical_game_id == gid,
                        OddsQuote.data_mode == mode.value,
                    ).order_by(OddsQuote.observed_at)
                )
            )
            markets: dict[str, Any] = {}
            for market in ("SPREAD", "TOTAL", "MONEYLINE"):
                snap, report = build_consensus(
                    s, canonical_game_id=gid, market=market, as_of_at=now,
                    kickoff_utc=o.kickoff_utc, data_mode=mode,
                )
                markets[market] = {
                    "consensus": None if snap is None else {
                        "median_line": snap.median_line,
                        "home_price_american": snap.home_price_american,
                        "away_price_american": snap.away_price_american,
                        "over_price_american": snap.over_price_american,
                        "under_price_american": snap.under_price_american,
                        "eligible_books": snap.eligible_books,
                        "observed_at": snap.observed_at.isoformat(),
                        "method_version": snap.method_version,
                    },
                    # The verdict is the payload, not an error path.
                    "reasons": list(report.reasons),
                    "eligible": report.eligible,
                    "considered": report.considered,
                }
            games.append({
                "canonical_game_id": gid,
                "away_team_id": o.away_team_id,
                "home_team_id": o.home_team_id,
                "kickoff_utc": o.kickoff_utc.isoformat(),
                "season": o.season,
                "season_type": o.season_type,
                "venue": o.stadium_name,
                "neutral_site": bool(o.neutral_site),
                "game_status": o.game_status,
                # Provenance travels with the row. A fixture attested only
                # by an odds provider is a weaker record than one from the
                # schedule feed, and the reader is entitled to know which.
                "schedule_provider": o.provider,
                "observed_at": o.observed_at.isoformat(),
                "books": sorted({q.sportsbook for q in quotes}),
                "quotes": [
                    {
                        "sportsbook": q.sportsbook, "market": q.market,
                        "selection": q.selection, "line": q.line,
                        "american": q.american, "decimal_odds": q.decimal_odds,
                        "provider": q.provider, "provider_mode": q.provider_mode,
                        "observed_at": q.observed_at.isoformat(),
                    }
                    for q in quotes
                ],
                "markets": markets,
            })

    return {
        "generated_at_utc": now.isoformat(),
        "data_mode": mode.value,
        "horizon_hours": hours,
        "games": games,
        "research_banner": RESEARCH_BANNER,
        "not_a_claim": (
            "Captured market data and the consensus builder's own verdict. "
            "No model probability, edge, or recommendation is included or "
            "implied. Nothing here is a claim about profitability or "
            "readiness for real money."
        ),
    }


@app.get("/v1/forward/health", dependencies=[Auth])
def get_forward_health(data_mode: str = "LIVE_RESEARCH") -> dict[str, Any]:
    """Data-health checks, grouped by the scope each one speaks for.

    Scope is the whole point: an expired provider key and a leaked future
    feature are both "unhealthy", and treating them as one number is how a
    platform problem gets mistaken for a reason to distrust a decision.
    """
    from fde_api.forward.health import HealthScope, run_health_checks, scope_of
    from fde_api.forward.modes import DataMode

    now = utc_now()
    report = run_health_checks(get_session(), now=now, data_mode=DataMode(data_mode))
    checks = report["checks"]

    grouped: dict[str, list[dict[str, Any]]] = {s.value: [] for s in HealthScope}
    for c in checks:
        try:
            scope = scope_of(c["id"]).value
        except Exception:
            scope = HealthScope.GOVERNANCE_INTEGRITY.value
        grouped[scope].append(c)

    return {
        "generated_at_utc": now.isoformat(),
        "data_mode": data_mode,
        "total": len(checks),
        "ok": sum(1 for c in checks if c.get("status") == "OK"),
        "by_scope": grouped,
        "worst_severity": (
            "CRITICAL" if any(c.get("severity") == "CRITICAL" and c.get("status") != "OK" for c in checks)
            else "WARNING" if any(c.get("severity") == "WARNING" and c.get("status") != "OK" for c in checks)
            else "OK"
        ),
        "research_banner": RESEARCH_BANNER,
    }


@app.get("/v1/forward/slate", dependencies=[Auth])
def get_forward_slate(data_mode: str = "LIVE_RESEARCH", limit: int = 400) -> dict[str, Any]:
    """The ingested slate, newest observation per game."""
    from fde_api.db.forward_models import ScheduleObservation
    from fde_api.forward.modes import DataMode

    mode = DataMode(data_mode)
    with session_scope() as s:
        rows = list(s.scalars(
            select(ScheduleObservation)
            .where(ScheduleObservation.data_mode == mode.value)
            .order_by(ScheduleObservation.kickoff_utc)
        ))
        latest: dict[str, Any] = {}
        for o in rows:
            latest[o.canonical_game_id] = o
        games = [
            {
                "canonical_game_id": o.canonical_game_id,
                "away_team_id": o.away_team_id,
                "home_team_id": o.home_team_id,
                "kickoff_utc": o.kickoff_utc.isoformat(),
                "season": o.season, "season_type": o.season_type, "week": o.week,
                "venue": o.stadium_name, "neutral_site": bool(o.neutral_site),
                "game_status": o.game_status, "schedule_provider": o.provider,
            }
            for o in sorted(latest.values(), key=lambda x: x.kickoff_utc)[:limit]
        ]
    return {
        "generated_at_utc": utc_now().isoformat(),
        "data_mode": mode.value,
        "count": len(games),
        "games": games,
        "research_banner": RESEARCH_BANNER,
    }
