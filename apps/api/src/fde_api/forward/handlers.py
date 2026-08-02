"""Executable scheduler job handlers.

Each handler is a thin adapter: it resolves what work is due, calls the
existing domain service, and reports a structured outcome. No business
logic lives here — duplicating it in the scheduler is how the two drift
apart.

Outcome vocabulary (the rules are declared, not implied):

  SUCCESS               work completed, nothing notable
  SUCCESS_WITH_WARNINGS completed, but something is degraded or absent
                        for a legitimate reason (forecast horizon, an
                        international venue, a market with too few books)
  SKIPPED               a precondition was absent, so no work was
                        attempted and nothing was written
  DATA_INCOMPLETE       work ran, but required inputs were missing, so
                        downstream consumers must not treat output as
                        actionable
  RETRYABLE_FAILURE     a transient fault; the scheduler will back off
  TERMINAL_FAILURE      a fault retrying cannot fix

The distinction that matters most: a missing provider key is SKIPPED,
never a failure and never a silent substitution. Fixture prices must not
appear in Live Research Mode because a credential was absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select

from fde_api.db.forward_models import (
    ForwardLedgerEntry,
    ManualBookPriceEntry,
    ScheduleObservation,
    Venue,
)
from fde_api.forward.cohort import ProviderMode
from fde_api.forward.consensus import build_all_consensus_for_game, closing_consensus
from fde_api.forward.scheduler import JobContext, JobResult, JobSkipped
from fde_api.forward.state import (
    DomainState,
    IllegalDomainStateError,
    Outcome,
)
from fde_api.forward.weather import (
    NwsClient,
    NwsUnavailable,
    VenueNotSupported,
    capture_forecast_for_game,
)


class WeatherStatus(StrEnum):
    CAPTURED = "CAPTURED"
    UNCHANGED = "UNCHANGED"
    NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"  # beyond the NWS forecast horizon
    NOT_APPLICABLE = "NOT_APPLICABLE"  # dome, or a venue NWS does not cover
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


# NWS publishes roughly a week ahead; beyond that there is nothing to fetch.
NWS_HORIZON = timedelta(days=7)


@dataclass
class HandlerResult:
    """Structured outcome persisted to the scheduler run record.

    Two independent axes: `outcome` is execution, `domain_state` is data.
    """

    outcome: Outcome
    domain_state: DomainState = DomainState.COMPLETE
    records_read: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    provider_calls: int = 0
    provider_credits: int = 0
    provider_mode: ProviderMode | None = None
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_summary: str | None = None
    lineage: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.domain_state is DomainState.UNKNOWN_LEGACY:
            raise IllegalDomainStateError(
                "UNKNOWN_LEGACY represents unreconstructable migrated history; "
                "a live handler must report an actual domain state"
            )

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_job_result(self) -> JobResult:
        return JobResult(
            records_read=self.records_read,
            records_written=self.records_created + self.records_updated,
            provider_calls=self.provider_calls,
            provider_credits=self.provider_credits,
            warnings=[*self.warnings,
                      f"outcome={self.outcome.value}",
                      f"domain_state={self.domain_state.value}"],
            detail={
                "outcome": self.outcome.value,
                "domain_state": self.domain_state.value,
                "records_created": self.records_created,
                "records_updated": self.records_updated,
                "records_skipped": self.records_skipped,
                "provider_mode": self.provider_mode.value if self.provider_mode else None,
                "warning_count": self.warning_count,
                "error_code": self.error_code,
                "error_summary": self.error_summary,
                "lineage": self.lineage,
                **self.detail,
            },
        )


# Declared dependency rules, asserted by tests so they cannot drift.
JOB_DEPENDENCY_RULES: dict[str, dict[str, tuple[str, str]]] = {
    "odds_capture": {
        "missing_key": (Outcome.SKIPPED.value, DomainState.DATA_INCOMPLETE.value),
        "provider_unavailable": (Outcome.RETRYABLE_FAILURE.value, DomainState.DATA_INCOMPLETE.value),
        "quota_exhausted": (Outcome.SKIPPED.value, DomainState.DATA_INCOMPLETE.value),
    },
    "weather_capture": {
        "beyond_horizon": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.NOT_YET_AVAILABLE.value),
        "international_venue": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.NOT_APPLICABLE.value),
        "dome": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.NOT_APPLICABLE.value),
        "nws_down": (Outcome.RETRYABLE_FAILURE.value, DomainState.DATA_INCOMPLETE.value),
    },
    "prediction_vintage": {
        "missing_quarterback": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.DATA_INCOMPLETE.value),
        "missing_consensus": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.DATA_INCOMPLETE.value),
        "cutoff_not_reached": (Outcome.SKIPPED.value, DomainState.NOT_YET_AVAILABLE.value),
        "health_suppressed": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.SUPPRESSED.value),
    },
    "settlement": {"no_final_score": (Outcome.SKIPPED.value, DomainState.NOT_YET_AVAILABLE.value)},
    "closing_capture": {
        "no_eligible_consensus": (Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.DATA_INCOMPLETE.value)
    },
}


def _kick(g: ScheduleObservation) -> datetime:
    """Kickoff of a game already filtered to have one."""
    assert g.kickoff_utc is not None
    return g.kickoff_utc


def _slate(ctx: JobContext, season: int = 2026) -> list[ScheduleObservation]:
    """Latest observation per game, scoped to this run's data mode."""
    by_game: dict[str, ScheduleObservation] = {}
    rows = ctx.session.scalars(
        select(ScheduleObservation)
        .where(
            ScheduleObservation.season == season,
            ScheduleObservation.data_mode == ctx.data_mode.value,
        )
        .order_by(ScheduleObservation.observed_at, ScheduleObservation.id)
    )
    for r in rows:
        by_game[r.canonical_game_id] = r
    return list(by_game.values())


def _upcoming(ctx: JobContext, within: timedelta | None = None) -> list[ScheduleObservation]:
    """Upcoming, playable games. Every returned row has a kickoff."""
    now = ctx.now()
    out = []
    for g in _slate(ctx):
        if g.kickoff_utc is None or g.game_status in ("CANCELLED", "POSTPONED"):
            continue
        if g.kickoff_utc < now:
            continue
        if within is not None and (g.kickoff_utc - now) > within:
            continue
        out.append(g)
    return out


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def schedule_refresh(ctx: JobContext) -> JobResult:
    """Re-observe the published schedule. Append-only; unchanged games
    write nothing."""
    from fde_api.config import settings
    from fde_api.forward.schedule import ingest_schedule
    from fde_api.raw import RawArtifactStore

    store = RawArtifactStore(settings.raw_dir)
    manifest = store.latest_success("nflverse", "games")
    if manifest is None:
        return HandlerResult(
            outcome=Outcome.SKIPPED,
            warnings=["no ingested schedule artifact available"],
        ).to_job_result()

    payload = store.artifact_path(manifest).read_bytes()
    res = ingest_schedule(
        ctx.session, payload, season=int(ctx.params.get("season", 2026)),
        source_manifest_version=manifest.version_id, data_mode=ctx.data_mode,
        observed_at=ctx.now(),
    )
    warnings = list(res.warnings)
    if res.unmapped_stadiums:
        warnings.append(f"unmapped venues: {sorted(res.unmapped_stadiums)}")
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        records_read=res.observed_games,
        records_created=res.new_games + res.revisions,
        records_skipped=res.unchanged,
        warnings=warnings,
        lineage={"source_manifest_version": manifest.version_id},
        detail={"revisions": res.revisions, "new_games": res.new_games},
    ).to_job_result()


def odds_capture(ctx: JobContext) -> JobResult:
    """Capture market quotes.

    A missing credential is SKIPPED, not a failure — and emphatically not
    a reason to substitute fixture prices in Live Research Mode.
    """
    from fde_api.forward.odds import TheOddsApiProvider, capture_odds
    from fde_api.forward.quota import authorize_poll, record_usage

    if ctx.provider_mode is ProviderMode.KEY_MISSING:
        return HandlerResult(
            outcome=Outcome.SKIPPED,
            domain_state=DomainState.DATA_INCOMPLETE,
            provider_mode=ProviderMode.KEY_MISSING,
            records_created=0,
            warnings=["FDE_ODDS_API_KEY not configured; no market data captured"],
            error_code="PROVIDER_KEY_MISSING",
            error_summary="odds capture skipped: no provider credential",
        ).to_job_result()

    upcoming = _upcoming(ctx)
    if not upcoming:
        raise JobSkipped("no upcoming games eligible for polling")

    nearest_hours = min(
        (_kick(g) - ctx.now()).total_seconds() / 3600.0 for g in upcoming
    )
    decision = authorize_poll(
        ctx.session, hours_to_kickoff=nearest_hours,
        is_closing_capture=bool(ctx.params.get("closing")), now=ctx.now(),
    )
    if not decision.allowed:
        return HandlerResult(
            outcome=Outcome.SKIPPED,
            domain_state=DomainState.DATA_INCOMPLETE,
            provider_mode=ProviderMode.QUOTA_EXHAUSTED
            if decision.state == "EXHAUSTED"
            else ctx.provider_mode,
            warnings=[f"quota guard declined the poll: {decision.reason}"],
            detail={"quota_state": decision.state},
        ).to_job_result()

    if ctx.provider_mode is ProviderMode.FIXTURE:
        payload = ctx.params.get("fixture_payload")
        if payload is None:
            raise JobSkipped("fixture mode with no fixture payload supplied")
        headers: dict[str, str] = {}
        calls = 0
    else:
        provider = TheOddsApiProvider()
        try:
            payload, headers = provider.fetch_odds()
        except Exception as e:  # transient by default; the scheduler backs off
            return HandlerResult(
                outcome=Outcome.RETRYABLE_FAILURE,
                domain_state=DomainState.DATA_INCOMPLETE,
                provider_mode=ProviderMode.UNAVAILABLE,
                error_code="PROVIDER_UNAVAILABLE",
                error_summary=f"{type(e).__name__}: {e}",
                warnings=["provider request failed; no quotes written"],
            ).to_job_result()
        calls = 1
        record_usage(ctx.session, provider="the-odds-api", headers=headers, now=ctx.now())

    res = capture_odds(
        ctx.session, payload, request_id=ctx.idempotency_key,
        data_mode=ctx.data_mode, provider_mode=ctx.provider_mode,
        observed_at=ctx.now(),
    )
    warnings = list(res.warnings)
    if res.unmapped_events:
        warnings.append(f"unmapped provider events: {len(res.unmapped_events)}")
    if res.invalid_skipped:
        warnings.append(f"{res.invalid_skipped} malformed quotes rejected")
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        records_read=res.quotes_received,
        records_created=res.quotes_written,
        records_skipped=res.duplicates_skipped,
        provider_calls=calls,
        provider_credits=calls * 3,
        provider_mode=ctx.provider_mode,
        warnings=warnings,
        lineage={"request_id": ctx.idempotency_key},
        detail={"events_seen": res.events_seen},
    ).to_job_result()


def consensus_build(ctx: JobContext) -> JobResult:
    """Derive consensus snapshots from captured quotes."""
    games = _upcoming(ctx)
    if not games:
        raise JobSkipped("no upcoming games")
    created = 0
    warnings: list[str] = []
    lineage: dict[str, Any] = {}
    for g in games:
        out = build_all_consensus_for_game(
            ctx.session, canonical_game_id=g.canonical_game_id,
            kickoff_utc=g.kickoff_utc, as_of_at=ctx.now(), data_mode=ctx.data_mode,
        )
        lineage[g.canonical_game_id] = {m: v["snapshot_id"] for m, v in out.items()}
        for market, v in out.items():
            if v["snapshot_id"] is None:
                warnings.append(f"{g.canonical_game_id}/{market}: {'; '.join(v['reasons']) or 'ineligible'}")
            else:
                created += 1
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        records_read=len(games),
        records_created=created,
        warnings=warnings[:25],
        lineage=lineage,
    ).to_job_result()


def weather_capture(ctx: JobContext) -> JobResult:
    """Capture NWS forecast vintages.

    Beyond the forecast horizon and non-US venues are legitimate absences,
    not infrastructure failures.
    """
    games = _upcoming(ctx)
    if not games:
        raise JobSkipped("no upcoming games")

    client: NwsClient | None = ctx.params.get("nws_client")
    if client is None and ctx.provider_mode is ProviderMode.FIXTURE:
        raise JobSkipped("fixture mode with no NWS client supplied")
    client = client or NwsClient()

    created = unchanged = 0
    statuses: dict[str, str] = {}
    warnings: list[str] = []
    calls = 0
    for g in games:
        venue = ctx.session.get(Venue, g.stadium_id) if g.stadium_id else None
        if venue is None:
            statuses[g.canonical_game_id] = WeatherStatus.NOT_APPLICABLE.value
            warnings.append(f"{g.canonical_game_id}: venue unresolved")
            continue
        if not venue.weather_applicable:
            statuses[g.canonical_game_id] = WeatherStatus.NOT_APPLICABLE.value
            continue
        if venue.country != "US":
            statuses[g.canonical_game_id] = WeatherStatus.NOT_APPLICABLE.value
            warnings.append(f"{g.canonical_game_id}: {venue.country} venue, NWS does not cover it")
            continue
        if (_kick(g) - ctx.now()) > NWS_HORIZON:
            statuses[g.canonical_game_id] = WeatherStatus.NOT_YET_AVAILABLE.value
            continue
        try:
            calls += 1
            v = capture_forecast_for_game(
                ctx.session, canonical_game_id=g.canonical_game_id, venue=venue,
                kickoff_utc=_kick(g), client=client, data_mode=ctx.data_mode,
                observed_at=ctx.now(),
            )
            if v is None:
                unchanged += 1
                statuses[g.canonical_game_id] = WeatherStatus.UNCHANGED.value
            else:
                created += 1
                statuses[g.canonical_game_id] = WeatherStatus.CAPTURED.value
        except VenueNotSupported as e:
            statuses[g.canonical_game_id] = WeatherStatus.NOT_APPLICABLE.value
            warnings.append(f"{g.canonical_game_id}: {e}")
        except NwsUnavailable as e:
            statuses[g.canonical_game_id] = WeatherStatus.PROVIDER_UNAVAILABLE.value
            warnings.append(f"{g.canonical_game_id}: {e}")

    if any(s == WeatherStatus.PROVIDER_UNAVAILABLE.value for s in statuses.values()) and created == 0:
        return HandlerResult(
            outcome=Outcome.RETRYABLE_FAILURE,
            domain_state=DomainState.DATA_INCOMPLETE,
            provider_calls=calls,
            error_code="NWS_UNAVAILABLE",
            error_summary="every NWS request failed",
            warnings=warnings[:25],
            detail={"weather_status": statuses},
        ).to_job_result()

    values = set(statuses.values())
    if created or unchanged:
        domain = DomainState.COMPLETE
    elif values and values <= {WeatherStatus.NOT_APPLICABLE.value}:
        domain = DomainState.NOT_APPLICABLE
    elif WeatherStatus.NOT_YET_AVAILABLE.value in values:
        domain = DomainState.NOT_YET_AVAILABLE
    elif not statuses:
        domain = DomainState.NO_ELIGIBLE_RECORDS
    else:
        domain = DomainState.DATA_INCOMPLETE

    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        domain_state=domain,
        records_read=len(games),
        records_created=created,
        records_skipped=unchanged,
        provider_calls=calls,
        provider_mode=ctx.provider_mode,
        warnings=warnings[:25],
        detail={"weather_status": statuses},
    ).to_job_result()


def injury_reconciliation(ctx: JobContext) -> JobResult:
    """Summarize verified injury observations. Entry is manual by design;
    this job reports coverage rather than fetching anything."""
    from fde_api.forward.injuries import observations_as_of

    games = _upcoming(ctx, within=timedelta(days=10))
    if not games:
        raise JobSkipped("no games within the injury-reporting window")
    covered = 0
    warnings: list[str] = []
    for g in games:
        obs = observations_as_of(
            ctx.session, canonical_game_id=g.canonical_game_id,
            as_of_at=ctx.now(), data_mode=ctx.data_mode,
        )
        if obs:
            covered += 1
        else:
            warnings.append(f"{g.canonical_game_id}: no verified injury observations")
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        records_read=len(games),
        records_updated=covered,
        warnings=warnings[:25],
        detail={"games_with_injury_data": covered, "games_without": len(games) - covered},
    ).to_job_result()


def availability_computation(ctx: JobContext) -> JobResult:
    """Recompute availability assessments from current observations."""
    from fde_api.forward.injuries import assess_player, observations_as_of

    games = _upcoming(ctx, within=timedelta(days=8))
    if not games:
        raise JobSkipped("no games in the availability window")
    created = 0
    for g in games:
        for o in observations_as_of(
            ctx.session, canonical_game_id=g.canonical_game_id,
            as_of_at=ctx.now(), data_mode=ctx.data_mode,
        ):
            assess_player(
                ctx.session, canonical_game_id=g.canonical_game_id, team_id=o.team_id,
                player_id=o.player_id, as_of_at=ctx.now(), data_mode=ctx.data_mode,
            )
            created += 1
    return HandlerResult(
        outcome=Outcome.SUCCESS if created else Outcome.SUCCESS_WITH_WARNINGS,
        records_read=len(games),
        records_created=created,
        warnings=[] if created else ["no verified observations to assess"],
    ).to_job_result()


def feature_snapshot(ctx: JobContext) -> JobResult:
    """Feature snapshots are built inside vintage generation, which owns
    the point-in-time cutoff. This job reports readiness rather than
    duplicating that logic."""
    games = _upcoming(ctx, within=timedelta(days=8))
    if not games:
        raise JobSkipped("no games in the feature window")
    return HandlerResult(
        outcome=Outcome.SUCCESS,
        records_read=len(games),
        detail={"note": "snapshots are produced by prediction_vintage at its cutoff"},
    ).to_job_result()


def prediction_vintage(ctx: JobContext) -> JobResult:
    """Generate any vintage whose cutoff has arrived.

    Missing inputs produce a DATA_INCOMPLETE vintage that records why —
    the vintage is still written, because "we could not predict this" is
    itself a result worth keeping.
    """
    from fde_api.forward.policy import load_policy
    from fde_api.forward.vintages import (
        PREDICTION_HORIZONS,
        VintageImmutabilityError,
        generate_vintage,
        horizon_cutoff,
    )

    if not ctx.policy_version:
        raise JobSkipped("no forward-test policy in force")
    policy = load_policy(ctx.session, ctx.policy_version)

    created = skipped = incomplete = 0
    warnings: list[str] = []
    lineage: dict[str, Any] = {}
    moments = ctx.params.get("moments")

    for g in _slate(ctx):
        if g.kickoff_utc is None or g.game_status in ("CANCELLED", "POSTPONED"):
            continue
        for horizon in PREDICTION_HORIZONS:
            if horizon_cutoff(g.kickoff_utc, horizon) > ctx.now():
                skipped += 1
                continue
            try:
                pred, inputs = generate_vintage(
                    ctx.session, game=g, horizon=horizon, policy=policy,
                    moments=moments, data_mode=ctx.data_mode, now=ctx.now(),
                    expected_home_qb=ctx.params.get("home_qb"),
                    expected_away_qb=ctx.params.get("away_qb"),
                )
            except VintageImmutabilityError as e:
                return HandlerResult(
                    outcome=Outcome.TERMINAL_FAILURE,
                    error_code="VINTAGE_IMMUTABILITY",
                    error_summary=str(e),
                ).to_job_result()
            if pred is None:
                skipped += 1
                continue
            created += 1
            lineage[pred.id] = pred.lineage
            if pred.data_completeness < policy.missing_data.min_data_completeness_for_candidate:
                incomplete += 1
                warnings.append(
                    f"{pred.id}: completeness {pred.data_completeness:.2f} "
                    f"({'; '.join(inputs.warnings[:2])})"
                )

    # Execution succeeded either way; thin data is a DOMAIN state.
    outcome = Outcome.SUCCESS_WITH_WARNINGS if (incomplete or warnings) else Outcome.SUCCESS
    domain = DomainState.DATA_INCOMPLETE if incomplete else DomainState.COMPLETE
    return HandlerResult(
        outcome=outcome,
        domain_state=domain,
        records_created=created,
        records_skipped=skipped,
        warnings=warnings[:25],
        lineage=lineage,
        detail={"data_incomplete_vintages": incomplete},
    ).to_job_result()


def manual_price_expiration(ctx: JobContext) -> JobResult:
    """Mark manually entered prices that policy now considers stale.

    The price is never refreshed — bet365 is never contacted. Expiry only
    records that the observation can no longer support an evaluation.
    """
    from fde_api.forward.policy import load_policy

    if not ctx.policy_version:
        raise JobSkipped("no policy in force")
    policy = load_policy(ctx.session, ctx.policy_version)
    limit = timedelta(minutes=policy.price_staleness.max_manual_price_age_minutes)

    rows = ctx.session.scalars(
        select(ManualBookPriceEntry).where(
            ManualBookPriceEntry.data_mode == ctx.data_mode.value,
            ManualBookPriceEntry.superseded_by_id.is_(None),
        )
    ).all()
    stale = [r for r in rows if (ctx.now() - r.observed_at) > limit]
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if stale else Outcome.SUCCESS,
        records_read=len(rows),
        records_updated=0,
        warnings=[f"{r.id}: observed {(ctx.now() - r.observed_at)} ago, exceeds policy" for r in stale][:25],
        detail={"stale_entries": [r.id for r in stale],
                "note": "prices are never auto-refreshed; bet365 is never contacted"},
    ).to_job_result()


def closing_capture(ctx: JobContext) -> JobResult:
    """Record the rule-selected closing consensus for games at kickoff."""
    from fde_api.forward.policy import load_policy

    if not ctx.policy_version:
        raise JobSkipped("no policy in force")
    policy = load_policy(ctx.session, ctx.policy_version)
    window = timedelta(minutes=policy.closing_line.max_age_before_kickoff_minutes)

    due = [
        g for g in _slate(ctx)
        if g.kickoff_utc is not None
        and g.game_status not in ("CANCELLED", "POSTPONED")
        and (g.kickoff_utc - window) <= ctx.now() <= (g.kickoff_utc + timedelta(minutes=10))
    ]
    if not due:
        raise JobSkipped("no games inside the closing-capture window")

    found = 0
    warnings: list[str] = []
    for g in due:
        for market in policy.market_selection.markets:
            snap = closing_consensus(
                ctx.session, canonical_game_id=g.canonical_game_id, market=market,
                kickoff_utc=_kick(g),
                max_age_before_kickoff_minutes=policy.closing_line.max_age_before_kickoff_minutes,
                data_mode=ctx.data_mode,
            )
            if snap is None:
                warnings.append(f"{g.canonical_game_id}/{market}: no eligible closing consensus")
            else:
                found += 1
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        records_read=len(due),
        records_updated=found,
        warnings=warnings[:25],
        detail={"missing_close": len(warnings)},
    ).to_job_result()


def result_ingestion(ctx: JobContext) -> JobResult:
    """Bring in final scores for completed games."""
    scores: dict[str, tuple[int, int]] = ctx.params.get("final_scores") or {}
    played = [
        g for g in _slate(ctx)
        if g.kickoff_utc is not None and g.kickoff_utc < ctx.now()
        and g.game_status not in ("CANCELLED", "POSTPONED")
    ]
    if not played:
        raise JobSkipped("no completed games to ingest")
    matched = sum(1 for g in played if g.canonical_game_id in scores)
    return HandlerResult(
        outcome=Outcome.SUCCESS if matched else Outcome.SKIPPED,
        records_read=len(played),
        records_updated=matched,
        warnings=[] if matched else ["no final scores available yet"],
        detail={"games_with_scores": matched},
    ).to_job_result()


def settlement(ctx: JobContext) -> JobResult:
    """Settle filled ledger entries whose games have final scores.

    Idempotent at the domain level: `settle_entry` returns early when the
    row already carries a result, so a rerun cannot double-settle.
    """
    from fde_api.forward.ledger import settle_entry
    from fde_api.forward.policy import load_policy

    if not ctx.policy_version:
        raise JobSkipped("no policy in force")
    policy = load_policy(ctx.session, ctx.policy_version)
    scores: dict[str, tuple[int, int]] = ctx.params.get("final_scores") or {}
    if not scores:
        raise JobSkipped("no final scores supplied")

    kickoffs = {g.canonical_game_id: g.kickoff_utc for g in _slate(ctx)}
    rows = ctx.session.scalars(
        select(ForwardLedgerEntry).where(
            ForwardLedgerEntry.data_mode == ctx.data_mode.value,
            ForwardLedgerEntry.policy_version == ctx.policy_version,
            ForwardLedgerEntry.result.is_(None),
        )
    ).all()
    settled = already = 0
    for entry in rows:
        pair = scores.get(entry.canonical_game_id)
        kick = kickoffs.get(entry.canonical_game_id)
        if pair is None or kick is None:
            continue
        before = entry.result
        settle_entry(
            ctx.session, entry=entry, home_score=pair[0], away_score=pair[1],
            kickoff_utc=kick, policy=policy, data_mode=ctx.data_mode,
        )
        if before is None and entry.result is not None:
            settled += 1
        else:
            already += 1
    return HandlerResult(
        outcome=Outcome.SUCCESS,
        records_read=len(rows),
        records_updated=settled,
        records_skipped=already,
        detail={"settled": settled, "already_settled": already},
    ).to_job_result()


def forward_evaluation(ctx: JobContext) -> JobResult:
    """Recompute the forward cohort summary."""
    from fde_api.forward.ledger import cohort_summary

    if not ctx.policy_version:
        raise JobSkipped("no policy in force")
    summary = cohort_summary(
        ctx.session, policy_version=ctx.policy_version, data_mode=ctx.data_mode
    )
    warnings = [summary["sample_warning"]] if summary.get("sample_warning") else []
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if warnings else Outcome.SUCCESS,
        records_read=summary["total_rows"],
        warnings=warnings,
        detail={"summary": summary},
    ).to_job_result()


def data_health_reconciliation(ctx: JobContext) -> JobResult:
    """Run the health checks and surface suppression state."""
    from fde_api.forward.health import run_health_checks

    report = run_health_checks(
        ctx.session, data_mode=ctx.data_mode, provider_mode=ctx.provider_mode,
        policy_version=ctx.policy_version, now=ctx.now(),
    )
    critical = [c for c in report["checks"] if c["severity"] == "CRITICAL" and c["status"] != "OK"]
    return HandlerResult(
        outcome=Outcome.SUCCESS_WITH_WARNINGS if critical else Outcome.SUCCESS,
        domain_state=DomainState.SUPPRESSED if report["candidates_suppressed"] else DomainState.COMPLETE,
        records_read=len(report["checks"]),
        warnings=[f"{c['id']}: {c['explanation']}" for c in critical][:25],
        detail={
            "candidates_suppressed": report["candidates_suppressed"],
            "suppression_reasons": report["suppression_reasons"],
        },
    ).to_job_result()


HANDLERS = {
    "schedule_refresh": schedule_refresh,
    "odds_capture": odds_capture,
    "consensus_build": consensus_build,
    "weather_capture": weather_capture,
    "injury_reconciliation": injury_reconciliation,
    "availability_computation": availability_computation,
    "feature_snapshot": feature_snapshot,
    "prediction_vintage": prediction_vintage,
    "manual_price_expiration": manual_price_expiration,
    "closing_capture": closing_capture,
    "result_ingestion": result_ingestion,
    "settlement": settlement,
    "forward_evaluation": forward_evaluation,
    "data_health_reconciliation": data_health_reconciliation,
}


def register_all(scheduler: Any) -> None:
    """Attach every handler to a scheduler with its declared cadence."""
    from fde_api.forward.scheduler import (
        CRITICAL_JOBS,
        JOB_CADENCE,
        JOB_CATCH_UP,
        CatchUpPolicy,
        JobDefinition,
    )

    missing = sorted(set(HANDLERS) - set(JOB_CADENCE))
    if missing:
        # Previously this silently fell back to an hourly default, which is
        # how consensus_build ran an order of magnitude slower than the
        # quotes it summarises without anything reporting a problem. A job
        # with no declared cadence is a configuration error, not a job with
        # an hourly cadence.
        raise ValueError(
            f"jobs registered with no declared cadence: {missing}; "
            "add them to scheduler.JOB_CADENCE"
        )

    for name, handler in HANDLERS.items():
        cadence = JOB_CADENCE[name]
        scheduler.register(
            JobDefinition(
                name=name,
                handler=handler,
                interval=cadence,
                catch_up=JOB_CATCH_UP.get(name, CatchUpPolicy.RUN_LATEST_ONLY),
                critical=name in CRITICAL_JOBS,
                description=handler.__doc__ or "",
            )
        )
