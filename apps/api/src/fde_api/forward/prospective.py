"""Actual-time research forecasting using the existing registry and evidence tables.

No scheduled-time backdating, market inputs, model promotion or wagering.
Public-provider final observations are used only when uncontested; reviews block.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.backtest.research_comparison import score_history
from fde_api.canonical.team_map import CANONICAL_TEAMS
from fde_api.db.forward_models import GameIdentityReview, ScheduleObservation, WeatherForecastVintage
from fde_api.db.models import FeatureSnapshot, Game, Job, ModelEvaluation, ModelVersion, Prediction, Team
from fde_api.evaluation import brier, calibration_line, log_loss, reliability_bins
from fde_api.features.history import GameRow, LeagueHistory
from fde_api.forward.forecast_integrity import eligibility, freeze_forecast
from fde_api.forward.intelligence import IntelligenceAt, digest
from fde_api.forward.modes import DataMode
from fde_api.forward.results import _score_of, final_observation
from fde_api.models_ml.baselines import HomeFieldBaseline, RollingAverageBaseline
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.registry import register_model
from fde_api.util import current_code_commit

VERSION = "prospective-scores-v1"
CHECKPOINTS = {"PROS_EARLY": 168.0, "PROS_72H": 72.0, "PROS_24H": 24.0, "PROS_FINAL": 1.5}
LIMITATIONS = [
    "RESEARCH ONLY — NO BET; no model is approved for recommendations.",
    "Score-only forecasts omit unvalidated personnel, QB, weather, route and efficiency effects.",
    "Ratings and rolling models failed the historical market screen; prospective quality is unestablished.",
    "Normal marginal intervals approximate NFL scoring tails; this is not an SGP joint model.",
    "Public final observations are not a sportsbook settlement feed; disputed outcomes are withheld.",
]


def schedule_at(s: Session, now: datetime, game_ids: set[str] | None = None) -> tuple[dict[str, ScheduleObservation], set[str]]:
    if now.tzinfo is None:
        raise ValueError("Aware execution time required")
    from sqlalchemy import or_

    from fde_api.forward.read_queries import latest_schedule

    states = {}
    stmt = latest_schedule(
        ScheduleObservation.data_mode == "LIVE_RESEARCH", ScheduleObservation.observed_at <= now,
        or_(ScheduleObservation.source_updated_at.is_(None), ScheduleObservation.source_updated_at <= now),
    )
    if game_ids is not None:
        stmt = stmt.where(ScheduleObservation.canonical_game_id.in_(game_ids))
    for row in s.scalars(
        stmt
    ):
        if row.source_updated_at is None or row.source_updated_at <= now:
            states[row.canonical_game_id] = row
    blocked: set[str] = set()
    for review in s.scalars(
        select(GameIdentityReview).where(
            GameIdentityReview.data_mode == "LIVE_RESEARCH", GameIdentityReview.created_at <= now
        )
    ):
        blocked.update(
            str(v)
            for v in [
                review.evidence.get("canonical_game_id"),
                *review.evidence.get("candidates", []),
                *review.evidence.get("candidate_ids", []),
            ]
            if v
        )
        # Provider-specific identity ambiguity blocks every linked observation.
        blocked.update(
            g.canonical_game_id
            for g in states.values()
            if (g.provider, g.provider_game_id) == (review.provider, review.provider_game_id)
        )
    return states, blocked


def verified_final(
    s: Session, game: ScheduleObservation, now: datetime, blocked: set[str]
) -> tuple[ScheduleObservation, tuple[int, int]] | None:
    if game.canonical_game_id in blocked or game.game_status != "FINAL":
        return None
    obs = final_observation(
        s, canonical_game_id=game.canonical_game_id, data_mode=DataMode.LIVE_RESEARCH, as_of=now
    )
    if (
        obs is None
        or obs.provider not in {"espn", "nflverse"}
        or obs.kickoff_utc is None
        or obs.observed_at < obs.kickoff_utc
        or now < obs.kickoff_utc
        or (obs.source_updated_at is not None and obs.source_updated_at > now)
    ):
        return None
    canonical = s.get(Game, game.canonical_game_id)
    if (obs.home_team_id, obs.away_team_id, obs.kickoff_utc) != (game.home_team_id, game.away_team_id, game.kickoff_utc):
        return None
    if canonical and (canonical.home_team_id, canonical.away_team_id) != (game.home_team_id, game.away_team_id):
        return None
    home, away = _score_of(obs)
    if home is None or away is None or min(home, away) < 0:
        return None
    # Contradictory independent providers cannot be adjudicated by newest-row order.
    latest_by_provider = {}
    for other in s.scalars(
        select(ScheduleObservation)
        .where(
            ScheduleObservation.canonical_game_id == game.canonical_game_id,
            ScheduleObservation.data_mode == "LIVE_RESEARCH",
            ScheduleObservation.game_status == "FINAL",
            ScheduleObservation.observed_at <= now,
        )
        .order_by(ScheduleObservation.observed_at, ScheduleObservation.id)
    ):
        if other.provider in {"espn", "nflverse"}:
            latest_by_provider[other.provider] = _score_of(other)
    if any(score != (home, away) for score in latest_by_provider.values()):
        return None
    return obs, (home, away)


def game_row(
    g: ScheduleObservation, score: tuple[int, int] | None = None, received: datetime | None = None
) -> GameRow:
    assert g.kickoff_utc is not None
    return GameRow(
        g.canonical_game_id,
        g.season,
        g.week,
        g.season_type,
        g.kickoff_utc,
        g.home_team_id,
        g.away_team_id,
        score[0] if score else None,
        score[1] if score else None,
        received,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        g.stadium_id,
    )


def checkpoint(kickoff: datetime, now: datetime, checkpoints: dict[str, float]) -> str | None:
    if not checkpoints or any(
        k not in CHECKPOINTS or not math.isfinite(v) or v <= 0 for k, v in checkpoints.items()
    ):
        raise ValueError("Positive finite supported checkpoints required")
    lead = (kickoff - now).total_seconds() / 3600
    due = [(hours, name) for name, hours in checkpoints.items() if 0 < lead <= hours]
    return min(due)[1] if due else None


def generate_forecasts(
    s: Session, *, now: datetime | None = None, checkpoints: dict[str, float] | None = None
) -> dict[str, Any]:
    # Injectable clock is for controlled tests only; CLI has no historical-time option.
    actual_execution = now is None
    now = now or datetime.now(UTC)
    checkpoints = {
        key: float(value) for key, value in (CHECKPOINTS if checkpoints is None else checkpoints).items()
    }
    states, blocked = schedule_at(s, now)
    completed, training_ids = [], []
    for g in states.values():
        final = verified_final(s, g, now, blocked)
        if final and g.season_type == "REG":
            obs, score = final
            completed.append(game_row(g, score, obs.observed_at))
            training_ids.append(obs.id)
    history = score_history(LeagueHistory(completed, []))
    result: dict[str, Any] = {"created": 0, "unchanged": 0, "withheld": [], "training_games": len(completed)}
    if len(completed) < 32:
        result["withheld"].append("At least 32 uncontested observed final results required")
        return result
    intel = IntelligenceAt(s, now)
    training_key = digest(
        {
            "results": sorted(training_ids),
            "version": VERSION,
            "code": current_code_commit(),
            "calibration": "identity",
        }
    )
    approaches = [HomeFieldBaseline(), RollingAverageBaseline(), DynamicRatings(k=0.12)]
    for g in sorted(states.values(), key=lambda r: (r.kickoff_utc or now, r.canonical_game_id)):
        if g.kickoff_utc is None or g.game_status != "SCHEDULED" or g.season_type != "REG":
            continue
        horizon = checkpoint(g.kickoff_utc, now, checkpoints)
        if horizon is None:
            continue
        schedule_poll = intel.polls.get((g.season, "schedule"))
        schedule_received = (
            schedule_poll.captured_at
            if schedule_poll and schedule_poll.status == "SUCCESS"
            else g.observed_at
        )
        if g.canonical_game_id in blocked or now - schedule_received > timedelta(hours=24):
            result["withheld"].append(f"{g.canonical_game_id}: ambiguous identity or stale schedule")
            continue
        canonical = s.get(Game, g.canonical_game_id)
        if canonical is not None and (canonical.home_team_id, canonical.away_team_id) != (
            g.home_team_id,
            g.away_team_id,
        ):
            result["withheld"].append(f"{g.canonical_game_id}: canonical team identity conflict")
            continue
        if canonical is None:
            if len(g.canonical_game_id) > 24 or any(
                team not in CANONICAL_TEAMS for team in (g.home_team_id, g.away_team_id)
            ):
                result["withheld"].append(f"{g.canonical_game_id}: unsupported canonical identity")
                continue
            for team in (g.home_team_id, g.away_team_id):
                if s.get(Team, team) is None:
                    s.add(Team(id=team, name=CANONICAL_TEAMS[team]))
            s.flush()
            s.add(
                Game(
                    id=g.canonical_game_id,
                    season=g.season,
                    week=g.week,
                    game_type=g.season_type,
                    kickoff_utc=g.kickoff_utc,
                    home_team_id=g.home_team_id,
                    away_team_id=g.away_team_id,
                )
            )
            s.flush()
        research = intel.game(g)
        if horizon == "PROS_FINAL" and (
            any(not t["availability_complete"] for t in research["teams"])
            or now - schedule_received > timedelta(hours=6)
        ):
            result["withheld"].append(
                f"{g.canonical_game_id}: final checkpoint requires complete official availability"
            )
            continue
        # Capture evidence even when its effect is unvalidated, without using it numerically.
        evidence = {k: v for k, v in research.items() if k not in {"cutoff", "manifest_hash"}}
        weather = s.scalars(
            select(WeatherForecastVintage)
            .where(
                WeatherForecastVintage.data_mode == "LIVE_RESEARCH",
                WeatherForecastVintage.canonical_game_id == g.canonical_game_id,
                WeatherForecastVintage.observed_at <= now,
            )
            .order_by(WeatherForecastVintage.observed_at.desc(), WeatherForecastVintage.id.desc())
        ).first()
        if weather and (weather.source_updated_at is None or weather.source_updated_at <= now):
            evidence["weather_observation"] = {
                "id": weather.id,
                "captured_at": weather.observed_at.isoformat(),
                "issued_at": weather.source_updated_at.isoformat() if weather.source_updated_at else None,
                "raw_hash": weather.raw_hash,
                "temp_f": weather.temp_f,
                "wind_mph": weather.wind_mph,
                "used_numerically": False,
            }
        signature = digest(
            {"training": training_key, "schedule": g.id, "evidence": evidence, "checkpoints": checkpoints}
        )
        feature_id = "pros-f-" + digest([g.canonical_game_id, horizon, signature])[:48]
        existing = s.get(FeatureSnapshot, feature_id)
        if existing:
            result["unchanged"] += 1
            continue
        features = FeatureSnapshot(
            id=feature_id,
            game_id=g.canonical_game_id,
            feature_set=VERSION,
            horizon=horizon,
            as_of_at=now,
            created_at=now,
            code_commit=current_code_commit(),
            content_hash=signature,
            values={
                "kickoff_at_creation": g.kickoff_utc.isoformat(),
                "schedule_observation_id": g.id,
                "training_result_observation_ids": sorted(training_ids),
                "training_results": [
                    {
                        "game_id": row.id,
                        "home_score": row.home_score,
                        "away_score": row.away_score,
                        "result_received_at": row.result_observed_at.isoformat()
                        if row.result_observed_at
                        else None,
                    }
                    for row in history.games
                ],
                "training_cutoff": now.isoformat(),
                "training_fingerprint": history.fingerprint(),
                "research": evidence,
                "checkpoint_hours": checkpoints[horizon],
                "execution_time": now.isoformat(),
                "calibration": "identity",
            },
            unavailable_features={"missing": research["missing"], "unvalidated": LIMITATIONS},
        )
        s.add(features)
        s.flush()
        for model in approaches:
            base_name = model.name
            model.fit(history.games, {}, history)
            model.name = "pros-" + base_name[:24] + "-" + training_key[:24]
            version = s.get(ModelVersion, model.name)
            if version is None:
                version = register_model(
                    s,
                    model,
                    target="prospective margin+total",
                    train_window="observed finals only",
                    validation_window="fixed prior research config",
                    test_window="future games only",
                    feature_set=VERSION,
                    calibration_version=None,
                    dataset_hashes={"training": training_key},
                    metrics={},
                    known_limitations="; ".join(LIMITATIONS),
                    random_seed=20260801,
                )
            if version.approval_status != "research_only":
                raise ValueError("Promoted model not eligible for this research cohort")
            outputs: dict[str, Any] = dict(
                model.predict(game_row(g), None).distribution().outputs(None, None)
            )
            outputs.update(
                {
                    "research_only": True,
                    "prospective_version": VERSION,
                    "approach": base_name,
                    "validation_status": "RESEARCH ONLY" if "homefield" in base_name else "VALIDATION FAILED",
                    "approval_status": "research_only",
                    "limitations": LIMITATIONS,
                    "missing": research["missing"],
                    "scheduled_kickoff": g.kickoff_utc.isoformat(),
                    "training_games": len(completed),
                    "season": g.season,
                    "week": g.week,
                }
            )
            s.add(
                Prediction(
                    id="pros-p-" + digest([feature_id, version.id])[:48],
                    game_id=g.canonical_game_id,
                    model_version_id=version.id,
                    feature_snapshot_id=feature_id,
                    horizon=horizon,
                    as_of_at=now,
                    created_at=now,
                    outputs=outputs,
                )
            )
            s.flush()
            published = s.get(Prediction, "pros-p-" + digest([feature_id, version.id])[:48])
            freeze_forecast(s, published, now=datetime.now(UTC) if actual_execution else now)
            model.name = base_name
            result["created"] += 1
        if actual_execution and datetime.now(UTC) >= g.kickoff_utc:
            raise ValueError("Kickoff passed during forecasting; transaction must be rolled back")
        s.flush()
    return result


def run_forecast_cycle(
    factory: Callable[[], Session], *, checkpoints: dict[str, float], capture_succeeded: bool
) -> dict[str, Any]:
    """Bounded worker phase: failures roll back publication and remain auditable."""
    now = datetime.now(UTC)
    identity = str(uuid.uuid4())
    with factory() as s:
        s.add(
            Job(
                id=identity,
                kind="prospective_forecast",
                params={
                    "version": VERSION,
                    "checkpoints": checkpoints,
                    "capture_succeeded": capture_succeeded,
                },
                status="running",
                created_at=now,
                started_at=now,
            )
        )
        s.commit()
    try:
        with factory() as s:
            result = {
                "forecasts": generate_forecasts(s, checkpoints=checkpoints)
                if capture_succeeded
                else {"created": 0, "withheld": ["Source capture failed"]},
                "performance": evaluate_forecasts(s),
            }
            job = s.get(Job, identity)
            assert job is not None
            job.status, job.finished_at, job.result = "finished", datetime.now(UTC), result
            s.commit()
        return {"status": "finished", "run_id": identity, **result}
    except Exception as exc:
        with factory() as s:
            job = s.get(Job, identity)
            assert job is not None
            job.status, job.finished_at = "failed", datetime.now(UTC)
            job.error = f"{type(exc).__name__}: forecast publication rolled back"
            s.commit()
        return {"status": "failed", "run_id": identity}


def evaluate_forecasts(s: Session, *, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    states, blocked = schedule_at(s, now)
    added = 0
    for p in s.scalars(
        select(Prediction).where(Prediction.horizon.in_(CHECKPOINTS), Prediction.created_at <= now)
    ):
        g = states.get(p.game_id)
        feature = s.get(FeatureSnapshot, p.feature_snapshot_id) if p.feature_snapshot_id else None
        if not g or not feature or p.outputs.get("prospective_version") != VERSION:
            continue
        if eligibility(s, p, g):
            continue
        original_kickoff = datetime.fromisoformat(feature.values["kickoff_at_creation"])
        if (
            p.created_at != p.as_of_at
            or p.created_at >= original_kickoff
            or g.kickoff_utc is None
            or p.created_at >= g.kickoff_utc
        ):
            continue
        final = verified_final(s, g, now, blocked)
        if final is None:
            continue
        obs, (home, away) = final
        key = "pros-e-" + digest([p.id, obs.id])[:48]
        if s.get(ModelEvaluation, key):
            continue
        o = p.outputs
        margin, total = home - away, home + away
        tied = home == away
        probability = o["home_win_prob"]
        metrics = {
            "prediction_id": p.id,
            "result_observation_id": obs.id,
            "result_observed_at": obs.observed_at.isoformat(),
            "verification": "PUBLIC_PROVIDER_UNCONTESTED",
            "evaluated_at": now.isoformat(),
            "actual_margin": margin,
            "actual_total": total,
            "margin_signed_error": o["expected_margin"] - margin,
            "total_signed_error": o["expected_total"] - total,
            "predicted_winner": "HOME" if probability > .5 else "AWAY" if probability < .5 else "UNDECIDED",
            "actual_winner": "TIE" if tied else "HOME" if home > away else "AWAY",
            "winner_correct": None if tied or probability == .5 else (probability > .5) == (home > away),
            "predicted_winner_probability": max(probability, 1 - probability),
            "home_score": home,
            "away_score": away,
            "is_tie": tied,
            "brier": None if tied else brier([probability], [int(home > away)]),
            "log_loss": None if tied else log_loss([probability], [int(home > away)]),
            "margin_absolute_error": abs(margin - o["expected_margin"]),
            "total_absolute_error": abs(total - o["expected_total"]),
            "margin_covered": o["margin_p10"] <= margin <= o["margin_p90"],
            "total_covered": o["total_p10"] <= total <= o["total_p90"],
            "approach": o["approach"],
            "horizon": p.horizon,
            "season": o["season"],
            "week": o["week"],
            "correction_policy": "new result observation appends an evaluation; disputed results withheld",
        }
        s.add(
            ModelEvaluation(
                id=key,
                model_version_id=p.model_version_id,
                scope="prospective:" + p.horizon,
                prediction_id=p.id,
                result_observation_id=obs.id,
                metrics=metrics,
                sample_size=1,
                created_at=now,
            )
        )
        added += 1
    s.flush()
    return {"evaluations_added": added}


def read_prospective(s: Session, *, now: datetime, game_id: str | None = None) -> dict[str, Any]:
    stmt = select(Prediction).where(Prediction.horizon.in_(CHECKPOINTS), Prediction.created_at <= now)
    if game_id:
        stmt = stmt.where(Prediction.game_id == game_id)
    predictions = list(
        s.scalars(
            stmt.order_by(Prediction.created_at, Prediction.id)
        )
    )
    if game_id:
        predictions = [p for p in predictions if p.game_id == game_id]
    valid = [p for p in predictions if p.outputs.get("prospective_version") == VERSION]
    states, blocked = schedule_at(s, now, {p.game_id for p in valid})
    by_id = {p.id: p for p in valid}
    features = {
        f.id: f
        for f in s.scalars(
            select(FeatureSnapshot).where(
                FeatureSnapshot.id.in_({p.feature_snapshot_id for p in valid if p.feature_snapshot_id})
            )
        )
    }
    versions = {
        v.id: v
        for v in s.scalars(
            select(ModelVersion).where(ModelVersion.id.in_({p.model_version_id for p in valid}))
        )
    }
    finals = {
        gid: verified_final(s, states[gid], now, blocked) if gid in states else None
        for gid in {p.game_id for p in valid}
    }
    ids = {p.id for p in valid}
    latest_eval = {}
    for ev in s.scalars(
        select(ModelEvaluation)
        .where(ModelEvaluation.scope.like("prospective:%"), ModelEvaluation.created_at <= now)
        .order_by(ModelEvaluation.created_at, ModelEvaluation.id)
    ):
        pid = ev.metrics.get("prediction_id")
        if pid in ids:
            current_final = finals[by_id[pid].game_id]
            if (current_final and current_final[0].id == ev.metrics.get("result_observation_id")
                    and eligibility(s, by_id[pid], states.get(by_id[pid].game_id)) is None):
                latest_eval[pid] = ev.metrics
    # One most recent evidence version per game/approach/horizon for headline metrics.
    # All earlier versions and their individual evaluations remain inspectable.
    latest = {}
    for p in valid:
        if p.id in latest_eval:
            latest[(p.game_id, p.outputs["approach"], p.horizon)] = p
    grouped: dict[tuple[str, str, str, str], list[tuple[Prediction, dict[str, Any]]]] = defaultdict(list)
    for p in latest.values():
        e = latest_eval.get(p.id)
        if e:
            grouped[(p.outputs["approach"], p.horizon, str(e["season"]), str(e["week"]))].append((p, e))
            grouped[(p.outputs["approach"], p.horizon, str(e["season"]), "ALL")].append((p, e))
            grouped[(p.outputs["approach"], p.horizon, "ALL", "ALL")].append((p, e))
    performance = []
    for (approach, horizon, season, week), pairs in sorted(grouped.items()):
        binary = [(p, e) for p, e in pairs if not e["is_tie"]]
        probs = [p.outputs["home_win_prob"] for p, e in binary]
        outcomes = [int(e["home_score"] > e["away_score"]) for p, e in binary]
        n = len(pairs)
        # Do not fit a calibration line to a handful of outcomes or one class.
        calibration = (
            calibration_line(probs, outcomes) if len(probs) >= 100 and len(set(outcomes)) == 2 else None
        )
        performance.append(
            {
                "approach": approach,
                "horizon": horizon,
                "season": season,
                "week": week,
                "n": n,
                "n_moneyline": len(binary),
                "ties": n - len(binary),
                "brier": brier(probs, outcomes) if binary else None,
                "log_loss": log_loss(probs, outcomes) if binary else None,
                "margin_mae": sum(e["margin_absolute_error"] for p, e in pairs) / n,
                "total_mae": sum(e["total_absolute_error"] for p, e in pairs) / n,
                "margin_coverage80": sum(e["margin_covered"] for p, e in pairs) / n,
                "total_coverage80": sum(e["total_covered"] for p, e in pairs) / n,
                "calibration_intercept": calibration[0] if calibration else None,
                "calibration_slope": calibration[1] if calibration else None,
                "reliability": reliability_bins(probs, outcomes) if binary else [],
                "status": "INSUFFICIENT EVIDENCE",
            }
        )
    records = []
    prior: dict[tuple[str, str], Prediction] = {}
    latest_approach = {(p.game_id, p.outputs["approach"]): p.id for p in valid}
    for p in valid:
        g = states.get(p.game_id)
        current = (
            g is not None
            and g.game_status == "SCHEDULED"
            and g.kickoff_utc is not None
            and g.kickoff_utc > now
            and p.outputs.get("scheduled_kickoff") == g.kickoff_utc.isoformat()
            and latest_approach[(p.game_id, p.outputs["approach"])] == p.id
        )
        old = prior.get((p.game_id, p.outputs["approach"]))
        frozen = features.get(p.feature_snapshot_id) if p.feature_snapshot_id else None
        version = versions.get(p.model_version_id)
        integrity_issue = eligibility(s, p, g)
        pregame = bool(integrity_issue is None and g and g.kickoff_utc and p.created_at < g.kickoff_utc
                       and p.created_at < datetime.fromisoformat(p.outputs["scheduled_kickoff"])
                       and p.created_at == p.as_of_at)
        evaluation_state = (
            "BLOCKED" if integrity_issue or p.game_id in blocked
            or (g and g.game_status == "FINAL" and finals[p.game_id] is None)
            else "EVALUATED" if p.id in latest_eval
            else "FINAL - PENDING EVALUATION" if finals.get(p.game_id)
            else "PREGAME" if g and g.kickoff_utc and now < g.kickoff_utc
            else "AWAITING VERIFIED FINAL"
        )
        records.append(
            {
                "id": p.id,
                "game_id": p.game_id,
                "model_version": p.model_version_id,
                "horizon": p.horizon,
                "created_at": p.created_at.isoformat(),
                "outputs": p.outputs,
                "feature_snapshot_id": p.feature_snapshot_id,
                "lineage": {
                    "training_cutoff": frozen.values.get("training_cutoff") if frozen else None,
                    "source_versions": frozen.values.get("research", {}).get("sources", []) if frozen else [],
                    "parameters": version.hyperparameters if version else {},
                    "artifact_hash": version.artifact_hash if version else None,
                    "code_commit": version.code_commit if version else None,
                    "limitations": version.known_limitations if version else None,
                },
                "evaluation": latest_eval.get(p.id),
                "eligible_pregame_record": pregame and integrity_issue is None,
                "evaluation_state": evaluation_state,
                "verification_warning": integrity_issue or ("Final providers disagree or result is incomplete" if g and g.game_status == "FINAL" and finals[p.game_id] is None else None),
                "state": "REVIEW REQUIRED"
                if p.game_id in blocked
                else "NOT ELIGIBLE: REVISED KICKOFF"
                if not pregame
                else "RESULT DISPUTED"
                if g and g.game_status == "FINAL" and finals[p.game_id] is None
                else g.game_status
                if g
                else "UNKNOWN",
                "current": bool(
                    current and integrity_issue is None and p.game_id not in blocked and now - p.created_at < timedelta(hours=24)
                ),
                "change": None
                if old is None
                else {
                    "previous_prediction_id": old.id,
                    "home_win_probability_delta": p.outputs["home_win_prob"] - old.outputs["home_win_prob"],
                    "margin_delta": p.outputs["expected_margin"] - old.outputs["expected_margin"],
                    "total_delta": p.outputs["expected_total"] - old.outputs["expected_total"],
                },
            }
        )
        prior[(p.game_id, p.outputs["approach"])] = p
    return {
        "version": VERSION,
        "status": "RESEARCH ONLY",
        "decision": "NO BET",
        "as_of_at": now.isoformat(),
        "count": len(records),
        "records": list(reversed(records)),
        "performance": performance,
        "evaluated_forecasts": len(latest_eval),
        "limitations": LIMITATIONS,
        "sample_policy": "Latest eligible evidence per game, approach and horizon; horizons are separate repeated cohorts, never independent games.",
    }
