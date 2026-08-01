"""Data Health — a gating dependency, not a dashboard.

Critical failures suppress RESEARCH CANDIDATE generation at the domain
service, so a direct API call cannot bypass the gate by skipping the UI.
`enforce_candidate_gate()` is called by the evaluation path itself.

Each check reports what it is, how bad it is, when it last succeeded,
which games it affects, whether it suppresses candidates, and what to do
about it. A check that cannot answer those questions is not a check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import (
    ConsensusSnapshot,
    ForwardPrediction,
    InjuryObservation,
    OddsQuote,
    ScheduledJobRun,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.forward.cohort import ProviderMode
from fde_api.forward.modes import DataMode
from fde_api.util import utc_now

EXPECTED_2026_GAMES = 272
EXPECTED_INTERNATIONAL_GAMES = 9
EXPECTED_INTERNATIONAL_STADIUMS = 8


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class Status(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class HealthCheck:
    id: str
    severity: Severity
    status: Status
    explanation: str
    remediation: str
    last_success_at: datetime | None = None
    last_checked_at: datetime | None = None
    affected_games: list[str] = field(default_factory=list)
    suppresses_candidates: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "status": self.status.value,
            "explanation": self.explanation,
            "remediation": self.remediation,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "affected_games": self.affected_games[:50],
            "affected_game_count": len(self.affected_games),
            "suppresses_candidates": self.suppresses_candidates,
            "detail": self.detail,
        }


class CandidateSuppressedError(RuntimeError):
    """Raised by the domain service when health forbids candidate output."""


def _ok(cid: str, sev: Severity, explanation: str, now: datetime, **kw: Any) -> HealthCheck:
    return HealthCheck(id=cid, severity=sev, status=Status.OK, explanation=explanation,
                       remediation="none required", last_success_at=now, last_checked_at=now, **kw)


def _fail(
    cid: str, sev: Severity, explanation: str, remediation: str, now: datetime,
    *, status: Status = Status.FAILED, suppress: bool | None = None, **kw: Any
) -> HealthCheck:
    return HealthCheck(
        id=cid, severity=sev, status=status, explanation=explanation,
        remediation=remediation, last_checked_at=now,
        suppresses_candidates=(sev is Severity.CRITICAL) if suppress is None else suppress,
        **kw,
    )


def run_health_checks(
    session: Session,
    *,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    provider_mode: ProviderMode = ProviderMode.KEY_MISSING,
    policy_version: str | None = None,
    season: int = 2026,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    checks: list[HealthCheck] = []

    # ---- scheduler ------------------------------------------------------ #
    runs = session.scalars(
        select(ScheduledJobRun).order_by(ScheduledJobRun.created_at.desc()).limit(500)
    ).all()
    last_run = runs[0].created_at if runs else None
    if not runs:
        checks.append(_fail("scheduler_running", Severity.CRITICAL,
                            "no scheduler runs recorded", "start the scheduler process", now))
    else:
        stale = last_run is not None and (now - last_run) > timedelta(hours=2)
        checks.append(
            _fail("scheduler_heartbeat", Severity.CRITICAL,
                  f"last scheduler activity {last_run.isoformat() if last_run else 'never'}",
                  "confirm the scheduler process is alive", now, status=Status.DEGRADED)
            if stale else _ok("scheduler_heartbeat", Severity.CRITICAL,
                              f"last activity {last_run.isoformat() if last_run else 'never'}", now)
        )
    for label, statuses, sev in (
        ("failed_jobs", ("failed",), Severity.WARNING),
        ("dead_letter_jobs", ("dead_letter",), Severity.CRITICAL),
        ("interrupted_jobs", ("interrupted",), Severity.WARNING),
    ):
        n = sum(1 for r in runs if r.status in statuses)
        checks.append(
            _ok(label, sev, f"{n} runs", now, detail={"count": n}) if n == 0
            else _fail(label, sev, f"{n} run(s) in state {statuses[0]}",
                       "inspect run history and the error summaries", now,
                       status=Status.DEGRADED, detail={"count": n})
        )

    # ---- provider ------------------------------------------------------- #
    if provider_mode is ProviderMode.KEY_MISSING:
        checks.append(_fail("odds_key_configured", Severity.CRITICAL,
                            "FDE_ODDS_API_KEY is not configured; no market data can be captured",
                            "set FDE_ODDS_API_KEY in the backend environment", now))
    elif provider_mode in (ProviderMode.FIXTURE, ProviderMode.SANDBOX):
        checks.append(_fail("odds_key_configured", Severity.CRITICAL,
                            f"provider mode is {provider_mode.value}; not live market data",
                            "configure a live provider before official capture", now,
                            status=Status.DEGRADED))
    else:
        checks.append(_ok("odds_key_configured", Severity.CRITICAL, "live provider configured", now))
    checks.append(_ok("provider_mode", Severity.INFO, f"provider mode is {provider_mode.value}",
                      now, detail={"provider_mode": provider_mode.value}))

    from fde_api.forward.quota import QuotaConfig, classify, latest_remaining

    remaining = latest_remaining(session, "the-odds-api")
    qstate = classify(remaining, QuotaConfig())
    checks.append(
        _ok("provider_quota", Severity.WARNING, f"quota state {qstate} (remaining={remaining})",
            now, detail={"remaining": remaining, "state": qstate})
        if qstate == "OK"
        else _fail("provider_quota", Severity.WARNING,
                   f"quota state {qstate} (remaining={remaining})",
                   "reduce cadence or top up the provider plan", now,
                   status=Status.DEGRADED, detail={"remaining": remaining, "state": qstate})
    )

    # ---- schedule ------------------------------------------------------- #
    slate: dict[str, ScheduleObservation] = {}
    for o in session.scalars(
        select(ScheduleObservation)
        .where(ScheduleObservation.season == season,
               ScheduleObservation.data_mode == data_mode.value)
        .order_by(ScheduleObservation.observed_at, ScheduleObservation.id)
    ):
        slate[o.canonical_game_id] = o
    games = list(slate.values())

    checks.append(
        _ok("schedule_game_count", Severity.CRITICAL,
            f"{len(games)} games observed", now, detail={"count": len(games)})
        if len(games) == EXPECTED_2026_GAMES
        else _fail("schedule_game_count", Severity.CRITICAL,
                   f"{len(games)} games observed, expected {EXPECTED_2026_GAMES}",
                   "re-run schedule_refresh and inspect the source", now,
                   detail={"count": len(games), "expected": EXPECTED_2026_GAMES})
    )

    intl = [g for g in games if g.international]
    stadiums = {g.stadium_id for g in intl}
    checks.append(
        _ok("international_game_count", Severity.WARNING,
            f"{len(intl)} international games", now)
        if len(intl) == EXPECTED_INTERNATIONAL_GAMES
        else _fail("international_game_count", Severity.WARNING,
                   f"{len(intl)} international games, expected {EXPECTED_INTERNATIONAL_GAMES}",
                   "check venue name resolution", now, status=Status.DEGRADED,
                   affected_games=[g.canonical_game_id for g in intl])
    )
    checks.append(
        _ok("international_stadium_count", Severity.WARNING,
            f"{len(stadiums)} international stadiums", now)
        if len(stadiums) == EXPECTED_INTERNATIONAL_STADIUMS
        else _fail("international_stadium_count", Severity.WARNING,
                   f"{len(stadiums)} international stadiums, expected {EXPECTED_INTERNATIONAL_STADIUMS}",
                   "check the neutral-venue registry", now, status=Status.DEGRADED)
    )

    unmapped = [g.canonical_game_id for g in games if g.stadium_id is None]
    checks.append(
        _ok("unmapped_venues", Severity.WARNING, "every game has a resolved venue", now)
        if not unmapped
        else _fail("unmapped_venues", Severity.WARNING,
                   f"{len(unmapped)} games have no resolved venue",
                   "add the venue to the governed table", now,
                   status=Status.DEGRADED, affected_games=unmapped)
    )

    # An international game must never carry a domestic venue id.
    from fde_api.forward.venues import VENUES_BY_ID

    domestic_ids = {v.id for v in VENUES_BY_ID.values() if v.country == "US"}
    leaked = [g.canonical_game_id for g in intl if g.stadium_id in domestic_ids]
    checks.append(
        _ok("international_venue_metadata", Severity.CRITICAL,
            "no international game carries domestic venue metadata", now)
        if not leaked
        else _fail("international_venue_metadata", Severity.CRITICAL,
                   f"{len(leaked)} international games carry a domestic venue",
                   "fix venue resolution; weather would query the wrong location", now,
                   affected_games=leaked)
    )

    last_sched = session.scalar(
        select(func.max(ScheduleObservation.observed_at)).where(
            ScheduleObservation.data_mode == data_mode.value)
    )
    sched_stale = last_sched is None or (now - last_sched) > timedelta(days=2)
    checks.append(
        _fail("schedule_freshness", Severity.WARNING,
              f"schedule last observed {last_sched.isoformat() if last_sched else 'never'}",
              "run schedule_refresh", now, status=Status.DEGRADED)
        if sched_stale else
        _ok("schedule_freshness", Severity.WARNING,
           f"observed {last_sched.isoformat() if last_sched else 'never'}", now)
    )

    # ---- market --------------------------------------------------------- #
    last_odds = session.scalar(
        select(func.max(OddsQuote.observed_at)).where(OddsQuote.data_mode == data_mode.value)
    )
    odds_stale = last_odds is None or (now - last_odds) > timedelta(hours=6)
    checks.append(
        _fail("odds_freshness", Severity.CRITICAL,
              f"newest quote {last_odds.isoformat() if last_odds else 'none captured'}",
              "confirm the provider key and the odds_capture job", now,
              detail={"note": "a stale provider never silently reuses an old quote as current"})
        if odds_stale else
        _ok("odds_freshness", Severity.CRITICAL,
           f"newest quote {last_odds.isoformat() if last_odds else 'none'}", now)
    )

    upcoming = [g for g in games if g.kickoff_utc and g.kickoff_utc > now]
    with_consensus = set(
        session.scalars(
            select(ConsensusSnapshot.canonical_game_id).where(
                ConsensusSnapshot.data_mode == data_mode.value)
        )
    )
    missing_consensus = [g.canonical_game_id for g in upcoming[:60]
                         if g.canonical_game_id not in with_consensus]
    checks.append(
        _ok("consensus_availability", Severity.WARNING, "consensus present for upcoming games", now)
        if not missing_consensus
        else _fail("consensus_availability", Severity.WARNING,
                   f"{len(missing_consensus)} upcoming games have no consensus",
                   "capture odds; consensus needs the minimum eligible book count", now,
                   status=Status.DEGRADED, affected_games=missing_consensus)
    )

    # ---- weather / injuries --------------------------------------------- #
    last_wx = session.scalar(
        select(func.max(WeatherForecastVintage.observed_at)).where(
            WeatherForecastVintage.data_mode == data_mode.value)
    )
    checks.append(
        _ok("weather_freshness", Severity.INFO,
            f"newest vintage {last_wx.isoformat() if last_wx else 'none'}", now)
        if last_wx and (now - last_wx) < timedelta(hours=12)
        else _fail("weather_freshness", Severity.INFO,
                   f"newest weather vintage {last_wx.isoformat() if last_wx else 'none'}",
                   "run weather_capture for games inside the NWS horizon", now,
                   status=Status.DEGRADED)
    )

    last_inj = session.scalar(
        select(func.max(InjuryObservation.observed_at)).where(
            InjuryObservation.data_mode == data_mode.value)
    )
    checks.append(
        _ok("injury_freshness", Severity.WARNING,
            f"newest observation {last_inj.isoformat() if last_inj else 'none'}", now)
        if last_inj and (now - last_inj) < timedelta(days=3)
        else _fail("injury_freshness", Severity.WARNING,
                   f"newest injury observation {last_inj.isoformat() if last_inj else 'none'}",
                   "enter verified injury observations", now, status=Status.DEGRADED)
    )

    # ---- vintages ------------------------------------------------------- #
    vintage_games = set(
        session.scalars(
            select(ForwardPrediction.canonical_game_id).where(
                ForwardPrediction.data_mode == data_mode.value)
        )
    )
    due_without = [
        g.canonical_game_id for g in games
        if g.kickoff_utc and g.kickoff_utc - timedelta(days=6) <= now < g.kickoff_utc
        and g.canonical_game_id not in vintage_games
    ]
    checks.append(
        _ok("prediction_vintage_coverage", Severity.WARNING,
            "every game past its opening cutoff has a vintage", now)
        if not due_without
        else _fail("prediction_vintage_coverage", Severity.WARNING,
                   f"{len(due_without)} games past a cutoff have no vintage",
                   "run prediction_vintage", now, status=Status.DEGRADED,
                   affected_games=due_without)
    )

    # ---- integrity ------------------------------------------------------ #
    if policy_version:
        from fde_api.forward.policy import verify_all_policies

        results = verify_all_policies(session)
        broken = [r for r in results if not r["intact"]]
        checks.append(
            _ok("policy_hash_integrity", Severity.CRITICAL,
                f"{len(results)} policy artifact(s) verified", now)
            if not broken
            else _fail("policy_hash_integrity", Severity.CRITICAL,
                       f"{len(broken)} policy artifact(s) fail hash verification",
                       "the immutable policy record has been altered; investigate", now)
        )
    else:
        checks.append(_fail("policy_hash_integrity", Severity.CRITICAL,
                            "no forward-test policy in force",
                            "freeze a policy before capture", now))

    from fde_api.db.models import ModelVersion

    approvals = list(session.scalars(select(ModelVersion.approval_status)))
    non_research = [a for a in approvals if a != "research_only"]
    checks.append(
        _ok("model_artifact_integrity", Severity.CRITICAL,
            f"all {len(approvals)} model artifacts are research_only", now)
        if not non_research
        else _fail("model_artifact_integrity", Severity.CRITICAL,
                   f"{len(non_research)} artifacts are not research_only",
                   "no model is approved; investigate immediately", now)
    )

    try:
        session.execute(select(1))
        checks.append(_ok("database_readiness", Severity.CRITICAL, "database reachable", now))
    except Exception as e:  # pragma: no cover - exercised only on a broken DB
        checks.append(_fail("database_readiness", Severity.CRITICAL,
                            f"database unreachable: {e}", "restore connectivity", now))

    suppressing = [c for c in checks if c.suppresses_candidates and c.status is not Status.OK]
    return {
        "generated_at": now.isoformat(),
        "data_mode": data_mode.value,
        "provider_mode": provider_mode.value,
        "policy_version": policy_version,
        "checks": [c.as_dict() for c in checks],
        "summary": {
            "total": len(checks),
            "ok": sum(1 for c in checks if c.status is Status.OK),
            "degraded": sum(1 for c in checks if c.status is Status.DEGRADED),
            "failed": sum(1 for c in checks if c.status is Status.FAILED),
        },
        "candidates_suppressed": bool(suppressing),
        "suppression_reasons": [f"{c.id}: {c.explanation}" for c in suppressing],
    }


def enforce_candidate_gate(
    session: Session,
    *,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    provider_mode: ProviderMode = ProviderMode.KEY_MISSING,
    policy_version: str | None = None,
    now: datetime | None = None,
) -> None:
    """Domain-level gate.

    Called by the evaluation service itself, so a direct API call cannot
    obtain a RESEARCH CANDIDATE while health is critical — hiding them in
    the UI would leave the API a bypass.
    """
    report = run_health_checks(
        session, data_mode=data_mode, provider_mode=provider_mode,
        policy_version=policy_version, now=now,
    )
    if report["candidates_suppressed"]:
        raise CandidateSuppressedError(
            "RESEARCH CANDIDATE generation is suppressed by Data Health: "
            + "; ".join(report["suppression_reasons"][:5])
        )


def suppression_state(
    session: Session,
    *,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    provider_mode: ProviderMode = ProviderMode.KEY_MISSING,
    policy_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    report = run_health_checks(
        session, data_mode=data_mode, provider_mode=provider_mode,
        policy_version=policy_version, now=now,
    )
    return {
        "suppressed": report["candidates_suppressed"],
        "reasons": report["suppression_reasons"],
        "checked_at": report["generated_at"],
    }
