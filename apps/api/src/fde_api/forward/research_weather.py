"""Bounded NWS collection for verified outdoor US venues, never a model adjustment."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from fde_api.db.forward_models import Venue
from fde_api.forward.briefing import prepare_briefing
from fde_api.forward.venues import resolve_by_name
from fde_api.forward.weather import NwsClient, NwsUnavailable, capture_forecast_for_game


class CoveringForecastClient(NwsClient):
    """A nearest forecast outside kickoff is not a valid kickoff forecast."""

    def __init__(self, client: httpx.Client, kickoff: datetime, now: datetime):
        super().__init__(client)
        self.kickoff, self.now = kickoff, now

    def fetch_forecast(self, forecast_url: str) -> tuple[dict[str, Any], bytes]:
        body, raw = super().fetch_forecast(forecast_url)
        props = body.get("properties", {})
        updated = props.get("updated") or props.get("updateTime")
        if updated and datetime.fromisoformat(updated.replace("Z", "+00:00")) > self.now:
            raise NwsUnavailable("Future source issuance")
        covers = False
        for period in props.get("periods", []):
            if period.get("startTime") and period.get("endTime"):
                start = datetime.fromisoformat(period["startTime"])
                end = datetime.fromisoformat(period["endTime"])
                if start <= self.kickoff < end:
                    covers = True
        if not covers:
            raise NwsUnavailable("Forecast does not cover kickoff")
        return body, raw


def collect_weather(s: Session, *, now: datetime) -> dict[str, int]:
    counts = {"weather_captured": 0, "weather_unchanged": 0,
              "weather_unavailable": 0, "weather_not_attempted": 0}
    games = prepare_briefing(s, now=now)["games"]
    # Two calls per game, max six games; abort further calls on provider failure.
    attempts = 0
    stopped = False
    with httpx.Client(timeout=10, headers={"User-Agent": "FourthDownEdge private research",
                                         "Accept": "application/geo+json"}) as http:
        for game in games:
            kickoff = datetime.fromisoformat(game["kickoff_utc"])
            from fde_api.db.forward_models import ScheduleObservation

            observation = s.get(ScheduleObservation, game["schedule"]["record_id"])
            spec = resolve_by_name(observation.stadium_name) if observation else None
            if (stopped or attempts >= 6 or kickoff <= now or spec is None
                    or not spec.verified or spec.country != "US" or spec.roof_type != "OUTDOOR"):
                counts["weather_not_attempted"] += 1
                continue
            venue = s.get(Venue, spec.id)
            if venue is None:
                venue = Venue(id=spec.id, name=spec.name, latitude=spec.lat, longitude=spec.lon,
                              timezone=spec.tz, roof_type=spec.roof_type, surface=spec.surface,
                              weather_applicable=spec.weather_applicable, country=spec.country,
                              source="fde governed venue reference v1", verification_status="VERIFIED",
                              updated_at=now)
                s.add(venue)
                s.flush()
            if venue.verification_status != "VERIFIED" or venue.roof_type != "OUTDOOR":
                counts["weather_not_attempted"] += 1
                continue
            attempts += 1
            try:
                vintage = capture_forecast_for_game(s, canonical_game_id=game["canonical_game_id"],
                    venue=venue, kickoff_utc=kickoff, observed_at=now,
                    client=CoveringForecastClient(http, kickoff, now))
                counts["weather_captured" if vintage else "weather_unchanged"] += 1
            except (NwsUnavailable, ValueError, KeyError, TypeError):
                counts["weather_unavailable"] += 1
                stopped = True
    return counts
