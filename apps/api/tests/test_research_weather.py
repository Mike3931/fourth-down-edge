from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fde_api.forward.research_weather import CoveringForecastClient
from fde_api.forward.weather import NwsUnavailable

NOW = datetime(2026, 10, 13, 1, tzinfo=UTC)
KICKOFF = NOW + timedelta(hours=2)


@pytest.mark.parametrize("case", ["missing", "past", "future_issuance"])
def test_forecast_refuses_noncovering_or_future_evidence(case):
    start = KICKOFF - timedelta(hours=3 if case == "past" else 0)
    periods = [] if case == "missing" else [{"startTime": start.isoformat(),
                                            "endTime": (start + timedelta(hours=1)).isoformat()}]
    issuance = NOW + timedelta(hours=1) if case == "future_issuance" else NOW
    payload = {"properties": {"updated": issuance.isoformat(), "periods": periods}}
    with (httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http,
          pytest.raises(NwsUnavailable)):
        CoveringForecastClient(http, KICKOFF, NOW).fetch_forecast("https://api.weather.gov/test")


def test_forecast_accepts_only_a_period_covering_exact_kickoff():
    payload = {"properties": {"updated": NOW.isoformat(), "periods": [{
        "startTime": KICKOFF.isoformat(), "endTime": (KICKOFF + timedelta(hours=1)).isoformat(),
        "temperature": 55, "temperatureUnit": "F", "windSpeed": "5 mph"}]}}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        body, _ = CoveringForecastClient(http, KICKOFF, NOW).fetch_forecast("https://api.weather.gov/test")
    assert body == payload


@pytest.mark.parametrize("failure", [False, True])
def test_collection_is_bounded_and_stops_on_outage(monkeypatch, failure):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from fde_api.db.forward_models import ScheduleObservation
    from fde_api.db.models import Base
    from fde_api.forward import research_weather

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []

    def capture(*args, **kwargs):
        calls.append(kwargs["canonical_game_id"])
        if failure:
            raise NwsUnavailable("source down")
        return None

    monkeypatch.setattr(research_weather, "capture_forecast_for_game", capture)
    with Session(engine) as s:
        for i in range(10):
            s.add(ScheduleObservation(canonical_game_id=f"G{i}", data_mode="LIVE_RESEARCH",
                season=2026, season_type="REG", week=6, away_team_id="SEA", home_team_id="DEN",
                kickoff_utc=KICKOFF, provider="espn", provider_game_id=f"G{i}", content_hash=f"G{i}",
                stadium_name="Empower Field at Mile High", game_status="SCHEDULED", observed_at=NOW))
        s.flush()
        counts = research_weather.collect_weather(s, now=NOW)
    assert len(calls) == (1 if failure else 6)
    assert counts["weather_unavailable"] == int(failure)
    assert counts["weather_not_attempted"] == (9 if failure else 4)
