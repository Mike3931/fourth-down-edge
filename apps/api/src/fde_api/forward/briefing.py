"""Evidence-only preparation for Release 1B. No prediction or ledger writes.

The current quote schema cannot establish a TN offer or combinability. This
version deliberately cannot qualify a bet365 opportunity, even when an older
research cohort contains a candidate. Capture freshness is not price freshness.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import (
    ForwardLedgerEntry,
    GameIdentityReview,
    InjuryObservation,
    OddsQuote,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.db.models import Job
from fde_api.db.research_models import PlayerBinding, PlayerIdentityReview, ResearchPoll, ResearchSource
from fde_api.forward.intelligence import VERSION as INTELLIGENCE_VERSION
from fde_api.forward.intelligence import IntelligenceAt

VERSION = "war-room-foundation-v1"
TIMEZONE = "America/Chicago"
KIND = "war_room_research"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def prepare_briefing(s: Session, *, now: datetime) -> dict[str, Any]:
    """Complete seven-day window, point-in-time observations, no global row cap."""
    if now.tzinfo is None:
        raise ValueError("An aware cutoff is required")
    mode = "LIVE_RESEARCH"
    local_day = now.astimezone(ZoneInfo(TIMEZONE)).date()
    schedule: dict[str, ScheduleObservation] = {}
    for row in s.scalars(select(ScheduleObservation).where(
        ScheduleObservation.data_mode == mode, ScheduleObservation.observed_at <= now,
    ).order_by(ScheduleObservation.observed_at, ScheduleObservation.id)):
        schedule[row.canonical_game_id] = row
    games = [g for g in schedule.values() if g.kickoff_utc is not None
             and local_day <= g.kickoff_utc.astimezone(ZoneInfo(TIMEZONE)).date()
             and g.kickoff_utc <= now + timedelta(days=7)]
    games.sort(key=lambda g: (g.kickoff_utc, g.canonical_game_id))
    ids = {g.canonical_game_id for g in games}
    quotes: dict[str, list[OddsQuote]] = defaultdict(list)
    for q in s.scalars(select(OddsQuote).where(
        OddsQuote.data_mode == mode, OddsQuote.canonical_game_id.in_(ids),
        OddsQuote.observed_at <= now, OddsQuote.provider_mode == "LIVE",
    ).order_by(OddsQuote.observed_at, OddsQuote.id)):
        quotes[q.canonical_game_id].append(q)
    injuries: dict[str, list[InjuryObservation]] = defaultdict(list)
    for injury in s.scalars(select(InjuryObservation).where(
        InjuryObservation.data_mode == mode, InjuryObservation.canonical_game_id.in_(ids),
        InjuryObservation.observed_at <= now,
        InjuryObservation.entered_at <= now,
    )):
        injuries[injury.canonical_game_id].append(injury)
    weather: dict[str, WeatherForecastVintage] = {}
    for forecast in s.scalars(select(WeatherForecastVintage).where(
        WeatherForecastVintage.data_mode == mode,
        WeatherForecastVintage.canonical_game_id.in_(ids),
        WeatherForecastVintage.observed_at <= now,
    ).order_by(WeatherForecastVintage.observed_at, WeatherForecastVintage.id)):
        weather[forecast.canonical_game_id] = forecast
    assessments: dict[tuple[str, str, str, str | None, str], list[ForwardLedgerEntry]] = {}
    for entry in s.scalars(select(ForwardLedgerEntry).where(
        ForwardLedgerEntry.data_mode == mode, ForwardLedgerEntry.canonical_game_id.in_(ids),
        ForwardLedgerEntry.as_of_at <= now, ForwardLedgerEntry.created_at <= now,
    ).order_by(ForwardLedgerEntry.as_of_at, ForwardLedgerEntry.id)):
        key = (entry.cohort, entry.canonical_game_id, entry.market, entry.selection, entry.horizon)
        previous = assessments.get(key)
        if not previous or entry.as_of_at > previous[0].as_of_at:
            assessments[key] = [entry]
        elif entry.as_of_at == previous[0].as_of_at:
            previous.append(entry)  # Equal-time conflicts require an owner, not an ID tiebreak.
    prepared = []
    intelligence = IntelligenceAt(s, now)
    for game in games:
        assert game.kickoff_utc is not None
        gid = game.canonical_game_id
        research_inputs = intelligence.game(game)
        latest: dict[tuple[str, str, str, float | None], OddsQuote] = {}
        for q in quotes[gid]:
            latest[(q.sportsbook, q.market, q.selection, q.line)] = q
        captured = list(latest.values())
        records = [e for group in assessments.values() for e in group if e.canonical_game_id == gid]
        missing = ["Verified bet365 Tennessee prices", "Approved market-specific model evidence"]
        missing.extend(research_inputs["missing"])
        verified_injuries = [i for i in injuries[gid] if i.verification_status == "VERIFIED"
                            and i.source_category in {"OFFICIAL_VERIFIED", "LICENSED_PROVIDER"}
                            and i.source_updated_at is not None
                            and 0 <= (now - i.source_updated_at).total_seconds() <= 172800]
        if not verified_injuries:
            missing.append("Fresh verified player availability evidence")
        game_forecast = weather.get(gid)
        fresh_weather = (game_forecast is not None and game_forecast.source_updated_at is not None
                         and 0 <= (now - game_forecast.source_updated_at).total_seconds() <= 21600)
        if not fresh_weather:
            missing.append("Fresh source-timed game environment evidence")
        age = (now - game.observed_at).total_seconds()
        quote_age = min(((now - q.observed_at).total_seconds() for q in captured), default=None)
        if age > 86400:
            missing.append("Schedule capture older than 24 hours")
        if any(len(group) > 1 and group[0].canonical_game_id == gid
               for group in assessments.values()):
            missing.append("Equal-time research assessments require review; none was selected")
        prepared.append({
            "canonical_game_id": gid, "away_team_id": game.away_team_id,
            "home_team_id": game.home_team_id, "kickoff_utc": game.kickoff_utc.isoformat(),
            "game_status": game.game_status, "today": game.kickoff_utc.astimezone(
                ZoneInfo(TIMEZONE)).date() == local_day,
            "status": "DATA_INCOMPLETE", "missing": missing,
            "intelligence": research_inputs,
            "intelligence_evidence_hash": research_inputs["evidence_hash"],
            "schedule": {"source": game.provider, "record_id": game.id,
                         "captured_at": game.observed_at.isoformat(),
                         "source_updated_at": game.source_updated_at.isoformat()
                         if game.source_updated_at else None, "capture_age_seconds": age},
            "markets_observed": sorted({q.market for q in captured}),
            "books_observed": sorted({q.sportsbook for q in captured}),
            "quote_count": len(captured), "quote_capture_age_seconds": quote_age,
            "quote_evidence": sorted({q.raw_hash for q in captured}),
            "quotes_with_source_time": sum(q.provider_timestamp is not None for q in captured),
            "injury_observations": len(injuries[gid]),
            "weather_captured_at": weather[gid].observed_at.isoformat() if gid in weather else None,
            "weather": None if gid not in weather else {
                "source": weather[gid].provider,
                "record_id": weather[gid].id,
                "captured_at": weather[gid].observed_at.isoformat(),
                "source_updated_at": _iso(weather[gid].source_updated_at),
                "temp_f": weather[gid].temp_f, "wind_mph": weather[gid].wind_mph,
                "precip_probability": weather[gid].precip_probability,
                "forecast_period_start": _iso(weather[gid].forecast_period_start),
                "not_a_claim": "Forecast observation; no model adjustment or confirmed conditions",
            },
            "research_records": [{"id": e.id, "status": e.status, "cohort": e.cohort,
                                  "model_version": e.model_version, "policy_version": e.policy_version,
                                  "as_of_at": e.as_of_at.isoformat()} for e in records],
            "inputs": {"team_efficiency": "OBSERVED_RESEARCH_ONLY" if any(
                        t["recent_games"] for t in research_inputs["teams"]) else "UNAVAILABLE",
                       "player_usage": "OBSERVED_RESEARCH_ONLY" if any(
                        t["players"] for t in research_inputs["teams"]) else "UNAVAILABLE",
                       "availability": "VERIFIED_OBSERVATIONS_ONLY" if verified_injuries else "UNAVAILABLE",
                       "environment": "SOURCE_TIMED_OBSERVATIONS_ONLY" if fresh_weather else "UNAVAILABLE"},
        })
    today = [g for g in prepared if g["today"]]
    review_count = len(list(s.scalars(select(GameIdentityReview).where(
        GameIdentityReview.data_mode == mode, GameIdentityReview.created_at <= now))))
    covered = sum(bool(g["markets_observed"]) for g in today)
    identity_notice = (f" {review_count} game identity observation(s) require review; "
                       "capture coverage is incomplete." if review_count else "")
    return {"version": VERSION, "as_of_at": now.isoformat(), "timezone": TIMEZONE,
            "data_mode": mode, "today_count": len(today), "observed_market_games": covered,
            "verified_bet365_count": 0, "qualified_count": 0, "decision": "NO_BET",
            "briefing": f"{len(today)} captured NFL game(s) today. {covered} have published market "
                        "observations. No verified bet365 Tennessee opportunity qualifies. "
                        "Price and model requirements are not satisfied." + identity_notice,
            "identity_review_count": review_count,
            "games": prepared, "sgp": {"status": "UNAVAILABLE", "reasons": [
                "Exact bet365 combined pricing and combinability are unverified",
                "No approved joint-probability model"]}}


def read_briefing(s: Session, *, now: datetime) -> dict[str, Any]:
    """Live read plus immutable completed run snapshots. Failed attempts stay visible."""
    runs = list(s.scalars(select(Job).where(Job.kind == KIND, Job.created_at <= now)
                          .order_by(Job.created_at.desc(), Job.id.desc()).limit(3)))
    current = None
    if runs and runs[0].status == "finished" and runs[0].result and runs[0].finished_at:
        stored = runs[0].result["briefing"]
        cutoff = datetime.fromisoformat(stored["as_of_at"])
        if (timedelta(0) <= now - cutoff < timedelta(minutes=15)
                and cutoff.astimezone(ZoneInfo(TIMEZONE)).date() == now.astimezone(ZoneInfo(TIMEZONE)).date()
                and stored.get("games") and all(g.get("intelligence", {}).get("version") == INTELLIGENCE_VERSION
                                               for g in stored["games"])):
            watermarks = (ScheduleObservation.observed_at, OddsQuote.observed_at,
                InjuryObservation.entered_at, WeatherForecastVintage.observed_at,
                ForwardLedgerEntry.created_at, GameIdentityReview.created_at,
                ResearchSource.captured_at, ResearchPoll.captured_at,
                PlayerBinding.observed_at, PlayerIdentityReview.observed_at)
            # Future timestamps neither invalidate nor enter the cached cutoff.
            cache_changed = any((latest := s.scalar(select(func.max(column)).where(column <= now))) is not None
                          and latest > cutoff for column in watermarks)
            expired = any(src["status"] == "OBSERVED" and src["published_at"]
                          and now - datetime.fromisoformat(src["published_at"]) > timedelta(hours=48)
                          for game in stored["games"] for src in game["intelligence"]["sources"])
            expired = expired or any(game.get("weather") and game["inputs"]["environment"] == "SOURCE_TIMED_OBSERVATIONS_ONLY"
                and game["weather"]["source_updated_at"]
                and now - datetime.fromisoformat(game["weather"]["source_updated_at"]) > timedelta(hours=6)
                for game in stored["games"])
            expired = expired or any(item["published_at"]
                and now - datetime.fromisoformat(item["published_at"]) > timedelta(hours=48)
                for game in stored["games"] for team in game["intelligence"]["teams"]
                for item in team["verified_availability"])
            if not cache_changed and not expired:
                current = deepcopy(stored)
    if current is None:
        current = prepare_briefing(s, now=now)
    current["latest_run"] = None if not runs else {
        "id": runs[0].id, "status": runs[0].status,
        "started_at": runs[0].started_at.isoformat() if runs[0].started_at else None,
        "finished_at": runs[0].finished_at.isoformat() if runs[0].finished_at else None,
        "error": runs[0].error,
    }
    current["changes"] = []
    complete = [r for r in runs if r.status == "finished" and r.result]
    if len(complete) >= 2:
        old_result, new_result = complete[1].result, complete[0].result
        assert old_result is not None and new_result is not None
        previous = {g["canonical_game_id"]: g for g in old_result["briefing"]["games"]}
        for game in new_result["briefing"]["games"]:
            before = previous.get(game["canonical_game_id"])
            fields = ["kickoff_utc", "game_status", "markets_observed", "books_observed",
                      "injury_observations", "weather_captured_at", "quote_evidence", "intelligence_evidence_hash"]
            changed = [field for field in fields if before and before.get(field) != game.get(field)]
            if before is None or changed:
                current["changes"].append({"canonical_game_id": game["canonical_game_id"],
                                           "fields": changed or ["new_game"],
                                           "previous_run_id": complete[1].id,
                                           "current_run_id": complete[0].id})
    return current
