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
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

if TYPE_CHECKING:
    from fde_api.forward.consensus import EligibilityReport
    from fde_api.forward.modes import DataMode

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


def read_session() -> Iterator[Session]:
    """A session that is always returned to the pool.

    `get_session()` hands back a new Session and closes nothing, so every
    endpoint that called it bare leaked one pooled connection per request.
    The default pool is 5 with 10 overflow, so the fifteenth such request
    exhausted it: subsequent calls blocked for thirty seconds and then
    failed with `QueuePool limit of size 5 overflow 10 reached`.

    That is a service that stops answering after fifteen page loads. It
    surfaced as the Model Audit screen hanging on "Asking the engine…"
    after a day of ordinary use — not as an error anyone would connect to
    connection handling, which is why it had survived.

    `session_scope()` already did this correctly for the write paths. The
    read endpoints predate it. This dependency closes but does not commit:
    these are reads, and `tests/test_read_endpoints_are_read_only.py`
    exists to keep them that way.
    """
    session = get_session()
    try:
        yield session
    finally:
        session.close()


Db = Annotated[Session, Depends(read_session)]

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


def _load_job(job_id: str, s: Session) -> Job:
    job = s.get(Job, job_id)
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
def health(s: Db) -> schemas.HealthResponse:
    try:
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
def list_models(s: Db) -> list[schemas.ModelSummary]:
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
def get_model(model_version: str, s: Db) -> schemas.ModelDetail:
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
def ingest_nflverse_endpoint(req: schemas.IngestRequest, s: Db) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.ingest import ingest_nflverse

        with session_scope() as s:
            report = ingest_nflverse(s, req.seasons)
        return {"loads": report.loads, "errors": report.errors,
                "manifests": [m.version_id for m in report.manifests]}

    return _job_response(_load_job(_start_job("ingest_nflverse", req.model_dump(), work), s))


@app.post("/v1/features/build", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def build_features_endpoint(req: schemas.FeaturesBuildRequest, s: Db) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.backtest.walkforward import build_or_load_features
        from fde_api.features import LeagueHistory
        from fde_api.pit.clock import PredictionHorizon

        with session_scope() as s:
            history = LeagueHistory.load(s)
        feats = build_or_load_features(history, req.half_life_days, PredictionHorizon(req.horizon))
        return {"games_with_features": len(feats), "half_life_days": req.half_life_days,
                "horizon": req.horizon, "feature_set": "nfl-core-v1"}

    return _job_response(_load_job(_start_job("features_build", req.model_dump(), work), s))


@app.post("/v1/backtests/run", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def run_backtest_endpoint(req: schemas.BacktestRequest, s: Db) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.backtest.walkforward import WalkForwardConfig, run_walkforward

        with session_scope() as s:
            res = run_walkforward(s, WalkForwardConfig(test_season=req.test_season))
        return {"run_id": res["run_id"], "fold": res["fold"]}

    return _job_response(_load_job(_start_job("backtest_run", req.model_dump(), work), s))


@app.get("/v1/jobs/{job_id}", response_model=schemas.JobResponse, dependencies=[Auth])
def get_job(job_id: str, s: Db) -> schemas.JobResponse:
    return _job_response(_load_job(job_id, s))


@app.get("/v1/backtests/{run_id}", response_model=schemas.BacktestRunResponse, dependencies=[Auth])
def get_backtest(run_id: str, s: Db) -> schemas.BacktestRunResponse:
    run = s.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown backtest run {run_id}")
    return schemas.BacktestRunResponse(
        id=run.id, status=run.status, config=run.config, error=run.error,
        created_at=run.created_at, finished_at=run.finished_at,
    )


@app.get("/v1/backtests/{run_id}/metrics", response_model=schemas.BacktestMetricsResponse, dependencies=[Auth])
def get_backtest_metrics(run_id: str, s: Db) -> schemas.BacktestMetricsResponse:
    run = s.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown backtest run {run_id}")
    if run.status != "finished" or run.metrics is None:
        raise HTTPException(status_code=409, detail=f"Backtest {run_id} is {run.status}; metrics not available")
    return schemas.BacktestMetricsResponse(run_id=run.id, metrics=run.metrics, research_banner=RESEARCH_BANNER)


@app.post("/v1/predictions/generate", response_model=schemas.JobResponse, status_code=202, dependencies=[Auth])
def generate_predictions(req: schemas.GeneratePredictionsRequest, s: Db) -> schemas.JobResponse:
    def work() -> dict[str, Any]:
        from fde_api.services.generate import generate_prediction

        with session_scope() as s:
            pred_id = generate_prediction(s, req.game_id, req.model_version_id, req.horizon)
        return {"prediction_id": pred_id}

    return _job_response(_load_job(_start_job("predictions_generate", req.model_dump(), work), s))


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
def get_prediction(prediction_id: str, s: Db) -> schemas.PredictionResponse:
    p = s.get(Prediction, prediction_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Unknown prediction {prediction_id}")
    return _prediction_response(s, p)


@app.get("/v1/games/{game_id}/predictions", response_model=list[schemas.PredictionResponse], dependencies=[Auth])
def get_game_predictions(game_id: str, s: Db) -> list[schemas.PredictionResponse]:
    if s.get(Game, game_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown game {game_id}")
    preds = s.scalars(
        select(Prediction).where(Prediction.game_id == game_id).order_by(Prediction.as_of_at)
    ).all()
    return [_prediction_response(s, p) for p in preds]


@app.get("/v1/performance/calibration", response_model=schemas.CalibrationReportResponse, dependencies=[Auth])
def get_calibration(s: Db) -> schemas.CalibrationReportResponse:
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
def get_model_comparison(s: Db) -> schemas.ModelComparisonResponse:
    rows = [
        schemas.ModelComparisonRow(
            model_version_id=e.model_version_id, scope=e.scope, sample_size=e.sample_size, metrics=e.metrics
        )
        # Ordered by `created_at` LAST so that where a model+scope has more
        # than one evaluation, the rows arrive oldest-first and the reader
        # gets the same one every time. Without it the pair is returned in
        # whatever order the database chose, and the screen - which keeps
        # the first row it sees - displayed an arbitrary member of the pair.
        #
        # `team-ratings-v1` has exactly that: two evaluations per test
        # scope, the before and after of the deterministic-ordering
        # correction. CERTIFICATION.md states the earlier numbers are
        # SUPERSEDED and must not be cited, and the screen was showing
        # them. This ordering does not decide which is authoritative -
        # that is settled in the certification record, not by a timestamp -
        # it only makes the answer stable and lets the screen say which
        # run it is showing.
        for e in s.scalars(
            select(ModelEvaluation).order_by(
                ModelEvaluation.scope,
                ModelEvaluation.model_version_id,
                ModelEvaluation.created_at,
            )
        )
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


def _no_consensus_reasons(eligible_books: int, report: EligibilityReport) -> list[str]:
    """Why no consensus exists, said so a person can act on it.

    The screen showed "0 eligible book(s), minimum is 3" directly above a
    table headed "Captured quotes (6 from draftkings)". Both true, and
    together they read as a bug: six quotes are plainly present and the
    line above insists there are none. What is missing is the word
    BETWEEN them - the quotes exist and were EXCLUDED, for a reason the
    eligibility filter already computed and the endpoint then discarded.

    This adds no judgement and no new computation. It reports counters
    that were already there, and it never implies a consensus that does
    not exist: the first line still states the binding constraint.
    """
    reasons = [
        f"no consensus captured yet; {eligible_books} eligible "
        f"book(s) in the current window, minimum is 3"
    ]
    if report.considered == 0:
        reasons.append("no quotes have been captured for this market at all")
        return reasons
    # Ordered most-likely-actionable first. Each is stated only when it
    # actually accounts for something, so the list never pads.
    #
    # No entry here is a disjunction. The first two used to be one line
    # reading "older than the freshness window, or newer than the cutoff",
    # which handed the reader the code's uncertainty as though it were the
    # market's - and the two halves have opposite remedies. So do the next
    # two: a provider-flagged in-play quote and a post-kickoff quote were
    # reported together under the second one's wording.
    excluded = [
        (report.rejected_stale, "older than the freshness window"),
        (
            report.rejected_after_cutoff,
            "observed after the as-of cutoff — for a live read that means the "
            "timestamp is in the future, which capturing more often will not fix",
        ),
        (report.rejected_live, "flagged in-play by the provider"),
        (report.rejected_post_kickoff, "observed after kickoff, so in-play"),
        (report.rejected_unknown_book, "from a book that is not on the recognised list"),
        (report.rejected_invalid, "carrying an implausible price or line"),
        (report.rejected_duplicate, "superseded by a newer quote from the same book"),
    ]
    parts = [f"{n} {why}" for n, why in excluded if n]
    if parts:
        reasons.append(
            f"{report.considered} quote(s) were considered and excluded: " + "; ".join(parts)
        )
    return reasons


@app.get("/v1/forward/live", dependencies=[Auth])
def get_forward_live(
    # `hours` is a KICKOFF horizon, not a capture-age window: games
    # kicking off within it. At 72 it hid a slate captured minutes
    # earlier, because preseason and midweek fixtures are three to five
    # days out — so the screen read "no fixtures captured" while sixty
    # fresh quotes sat in the table. Ten days covers a full NFL week plus
    # lead time, and matches the candidates endpoint rather than
    # disagreeing with it.
    #
    # `lookback_hours` stays at 72: that one IS about the past, keeping a
    # finished game and its result on screen for three days.
    data_mode: str = "LIVE_RESEARCH", hours: int = 240, lookback_hours: int = 72
) -> dict[str, Any]:
    from datetime import timedelta

    from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote, ScheduleObservation
    from fde_api.forward.consensus import select_eligible_quotes
    from fde_api.forward.modes import DataMode
    from fde_api.forward.results import result_scores

    now = utc_now()
    mode = DataMode(data_mode)
    horizon = now + timedelta(hours=hours)

    with session_scope() as s:
        observations = list(
            s.scalars(
                select(ScheduleObservation).where(
                    ScheduleObservation.data_mode == mode.value,
                    # A six-hour lookback dropped a game the moment it
                    # finished - taking the result, the closing line and
                    # the CLV comparison off the screen at exactly the
                    # point they became the most informative thing on it.
                    ScheduleObservation.kickoff_utc >= now - timedelta(hours=lookback_hours),
                    ScheduleObservation.kickoff_utc <= horizon,
                ).order_by(ScheduleObservation.kickoff_utc)
            )
        )
        # One entry per game: the schedule is append-only, so the newest
        # observation of a fixture is the current one.
        latest: dict[str, Any] = {}
        for o in observations:
            latest[o.canonical_game_id] = o

        # The FINAL observation carries the score, so the newest
        # observation per game is not necessarily the one that has it.
        finals: dict[str, Any] = {
            obs.canonical_game_id: obs
            for obs in observations if obs.game_status == "FINAL"
        }

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
                # READ the consensus the scheduler captured. Calling
                # build_consensus here would PERSIST one on every request:
                # it upserts, `as_of_at` is the wall clock, so each call
                # creates a new logical identity. A page polling every 60
                # seconds across the slate would have manufactured
                # consensus records that no scheduled job produced, dated
                # to whenever someone happened to have the tab open - and
                # they would have entered the forward-test record as if
                # they were captures.
                snap = s.scalars(
                    select(ConsensusSnapshot).where(
                        ConsensusSnapshot.canonical_game_id == gid,
                        ConsensusSnapshot.market == market,
                        ConsensusSnapshot.data_mode == mode.value,
                    ).order_by(ConsensusSnapshot.observed_at.desc()).limit(1)
                ).first()

                market_quotes = [q for q in quotes if q.market == market]
                # Pure: filters a list and reports counts, writes nothing.
                eligible, report = select_eligible_quotes(
                    market_quotes, as_of_at=now, max_age_minutes=60,
                    kickoff_utc=o.kickoff_utc,
                )
                books = sorted({q.sportsbook for q in eligible})

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
                    # Why there is no consensus, computed without creating
                    # one. The count of eligible BOOKS is the binding
                    # constraint, not the count of quotes.
                    "reasons": (
                        [] if snap is not None
                        else _no_consensus_reasons(len(books), report)
                    ),
                    "eligible_books_now": len(books),
                    "eligible": report.eligible,
                    "considered": report.considered,
                }
            final = finals.get(gid)
            scores = (
                result_scores(s, canonical_game_id=gid, data_mode=mode)
                if final is not None else None
            )
            home_score, away_score = scores if scores else (None, None)
            games.append({
                "canonical_game_id": gid,
                "final": None if final is None else {
                    "home_score": home_score,
                    "away_score": away_score,
                    "observed_at": final.observed_at.isoformat(),
                    "provider": final.provider,
                },
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
            "Captured market data and the consensus the scheduler recorded. "
            "This endpoint reads; it never builds a consensus. No model "
            "probability, edge, or recommendation is included or implied. "
            "Nothing here is a claim about profitability or readiness for "
            "real money."
        ),
    }


@app.get("/v1/forward/health", dependencies=[Auth])
def get_forward_health(s: Db, data_mode: str = "LIVE_RESEARCH") -> dict[str, Any]:
    """Data-health checks, grouped by the scope each one speaks for.

    Scope is the whole point: an expired provider key and a leaked future
    feature are both "unhealthy", and treating them as one number is how a
    platform problem gets mistaken for a reason to distrust a decision.
    """
    from fde_api.forward.health import HealthScope, run_health_checks, scope_of
    from fde_api.forward.modes import DataMode

    now = utc_now()
    report = run_health_checks(s, now=now, data_mode=DataMode(data_mode))
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


# --------------------------------------------------------------------------- #
# Research candidates and the forward-test record
# --------------------------------------------------------------------------- #
#
# The two screens Phase 3 named and never built. Both READ the forward
# ledger; neither evaluates anything. An endpoint that computed a fresh
# candidate on GET would produce a recommendation with no scheduler run
# behind it, no policy attached, and no place in the record chain — which
# is precisely the shape `chain.py` exists to detect.


def _candidate_gate(s: Session, mode: DataMode) -> dict[str, Any]:
    """Whether candidates may be produced at all, and what is stopping it.

    Built from `evaluation_health_context` — the SAME function the
    evaluation path runs in `handlers.py` — so the screen and the engine
    apply one rule rather than two that disagree.

    They did disagree. This read `suppresses_candidates` alone, and
    `_fail()` set that from severity, so every CRITICAL check carried it
    whatever it was about. The evaluation path also required the check's
    SCOPE to be one that may suppress, recording an OPERATIONAL_PLATFORM
    failure as degradation and proceeding. With no odds key and no
    scheduler run the engine evaluated four games and wrote three
    DATA_INCOMPLETE rows while this reported a closed gate, which told
    the reader nothing had been attempted. `_fail()` applies the scope
    now, so the flag no longer says two things at once.

    The docstring here used to promise the screen could not "show a rosier
    answer than the engine enforces". The divergence went the other way,
    and a screen that claims the engine refused to look is not the safe
    direction of a wrong answer - it is a different wrong answer.

    Operational failures are still reported, under `degraded_by`. They are
    usually the real reason a slate is empty; they are simply not the
    reason the engine declined, because it did not decline.
    """
    from fde_api.forward.health import evaluation_health_context, run_health_checks

    report = run_health_checks(s, now=utc_now(), data_mode=mode)
    ctx = evaluation_health_context(report)
    by_id = {c["id"]: c for c in report["checks"]}

    def described(check_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {"check": cid,
             "explanation": by_id[cid]["explanation"],
             "remediation": by_id[cid]["remediation"]}
            for cid in check_ids if cid in by_id
        ]

    return {
        "open": not ctx.suppressed,
        "blocked_by": described(ctx.failing_check_ids),
        "degraded_by": described(ctx.operational_check_ids),
    }


@app.get("/v1/forward/candidates", dependencies=[Auth])
def get_forward_candidates(
    s: Db, data_mode: str = "LIVE_RESEARCH", hours: int = 240, limit: int = 200
) -> dict[str, Any]:
    """Research candidates recorded for games that have not kicked off.

    RESEARCH_CANDIDATE and WATCH only. There is no BET state anywhere in
    this engine and this endpoint does not invent one: a candidate is a
    row someone can go and read, not an instruction.

    One row per (game, market, selection, horizon) — the newest. The ledger
    is append-only, so a re-evaluation at a later horizon adds a row rather
    than replacing one, and showing every historical evaluation of the same
    selection would read as many separate opportunities.
    """
    from datetime import timedelta

    from fde_api.db.forward_models import ForwardLedgerEntry, ScheduleObservation
    from fde_api.forward.modes import DataMode

    mode = DataMode(data_mode)
    now = utc_now()
    horizon_end = now + timedelta(hours=hours)

    kickoffs: dict[str, ScheduleObservation] = {}
    for o in s.scalars(
        select(ScheduleObservation).where(
            ScheduleObservation.data_mode == mode.value,
            ScheduleObservation.kickoff_utc.isnot(None),
        ).order_by(ScheduleObservation.observed_at)
    ):
        kickoffs[o.canonical_game_id] = o

    newest: dict[tuple[str, str, str | None, str], ForwardLedgerEntry] = {}
    for e in s.scalars(
        select(ForwardLedgerEntry)
        .where(
            ForwardLedgerEntry.data_mode == mode.value,
            ForwardLedgerEntry.status.in_(("RESEARCH_CANDIDATE", "WATCH")),
        )
        .order_by(ForwardLedgerEntry.as_of_at, ForwardLedgerEntry.id)
    ):
        newest[(e.canonical_game_id, e.market, e.selection, e.horizon)] = e

    rows: list[dict[str, Any]] = []
    for e in newest.values():
        game = kickoffs.get(e.canonical_game_id)
        if game is None or game.kickoff_utc is None:
            continue
        if not (now <= game.kickoff_utc <= horizon_end):
            continue  # already started, or too far out to be actionable
        edge = (
            e.model_probability - e.break_even_probability
            if e.model_probability is not None and e.break_even_probability is not None
            else None
        )
        rows.append({
            "canonical_game_id": e.canonical_game_id,
            "away_team_id": game.away_team_id, "home_team_id": game.home_team_id,
            "kickoff_utc": game.kickoff_utc.isoformat(),
            "status": e.status, "market": e.market, "selection": e.selection,
            "horizon": e.horizon,
            "line": e.qualifying_line, "american": e.qualifying_american,
            "price_source": e.price_source, "price_age_seconds": e.price_age_seconds,
            "model_probability": e.model_probability,
            "conservative_probability": e.conservative_probability,
            "break_even_probability": e.break_even_probability,
            "edge": edge,
            "expected_value": e.expected_value,
            "policy_version": e.policy_version, "model_version": e.model_version,
            "as_of_at": e.as_of_at.isoformat() if e.as_of_at else None,
        })

    rows.sort(key=lambda r: (r["edge"] is None, -(r["edge"] or 0.0)))
    return {
        "generated_at_utc": now.isoformat(),
        "data_mode": mode.value,
        "horizon_hours": hours,
        "gate": _candidate_gate(s, mode),
        "count": len(rows),
        "candidates": rows[:limit],
        "research_banner": RESEARCH_BANNER,
        "not_a_claim": (
            "Research candidates recorded by the forward test. Not advice, not a "
            "wager, and not a claim that any price is currently available. No "
            "money is staked by this software and no BET state exists in it."
        ),
    }


@app.get("/v1/forward/performance", dependencies=[Auth])
def get_forward_performance(s: Db, data_mode: str = "LIVE_RESEARCH") -> dict[str, Any]:
    """The forward-test record, per frozen policy.

    Separate from the backtest and never merged with it: the backtest
    scored seasons that have since been read (docs/model-governance.md
    burns 2024 and 2025), while this scores a cohort collected
    prospectively under a policy frozen before it started. Presenting them
    together would let a burned number stand in for an unburned one.
    """
    from fde_api.db.forward_models import ForwardTestPolicyRecord
    from fde_api.forward.ledger import cohort_summary
    from fde_api.forward.modes import DataMode

    mode = DataMode(data_mode)
    cohorts = [
        cohort_summary(s, policy_version=p.policy_version, data_mode=mode)
        | {
            "window": {"start": p.start_date, "end": p.end_date},
            "model_version": p.model_version,
            "calibration_version": p.calibration_version,
            "policy_hash": p.policy_hash,
            "frozen_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in s.scalars(
            select(ForwardTestPolicyRecord).order_by(ForwardTestPolicyRecord.created_at)
        )
    ]
    return {
        "generated_at_utc": utc_now().isoformat(),
        "data_mode": mode.value,
        "cohorts": cohorts,
        "research_banner": RESEARCH_BANNER,
        "not_a_claim": (
            "Paper forward test. Every figure is simulated against recorded prices; "
            "no wager was placed. These numbers are not evidence of profitability, "
            "and a cohort of this size cannot support any claim about edge."
        ),
    }
