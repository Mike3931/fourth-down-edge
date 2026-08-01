"""Prospective NWS forecast capture.

The NWS publishes forecasts going forward only; there is no way to ask it
what it predicted last week. That is precisely why this must run
prospectively: every vintage we capture today becomes point-in-time
evidence later, and a forecast we failed to capture is simply
unavailable — never reconstructed from what actually happened.

Each capture stores the raw response, the NWS issuance time
(`source_updated_at`), and our local observation instant. Earlier
vintages are never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.config import settings
from fde_api.db.forward_models import RoofStateObservation, Venue, WeatherForecastVintage
from fde_api.forward.modes import DataMode
from fde_api.util import utc_now

NWS_BASE = "https://api.weather.gov"
USER_AGENT = "fourth-down-edge research (contact: repository owner)"


class VenueNotSupported(RuntimeError):
    """The venue is outside NWS coverage (international) or has no
    coordinates. Raised so the caller records DATA INCOMPLETE rather than
    silently producing no weather."""


class NwsUnavailable(RuntimeError):
    """NWS request failed. Distinct from 'venue unsupported' so an outage
    is never mistaken for a permanent gap."""


@dataclass
class WeatherCaptureResult:
    attempted: int = 0
    captured: int = 0
    unchanged: int = 0
    unsupported: int = 0
    failed: int = 0
    not_applicable: int = 0
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "captured": self.captured,
            "unchanged": self.unchanged,
            "unsupported": self.unsupported,
            "failed": self.failed,
            "not_applicable": self.not_applicable,
            "details": self.details[:50],
        }


class NwsClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
        )
        self._grid_cache: dict[tuple[float, float], dict[str, Any]] = {}

    def resolve_grid(self, lat: float, lon: float) -> dict[str, Any]:
        """Resolve coordinates to NWS office + grid + forecast endpoint."""
        key = (round(lat, 4), round(lon, 4))
        if key in self._grid_cache:
            return self._grid_cache[key]
        try:
            resp = self._client.get(f"{NWS_BASE}/points/{key[0]},{key[1]}")
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise VenueNotSupported(f"NWS does not cover {lat},{lon}") from e
            raise NwsUnavailable(f"NWS points lookup failed: {e}") from e
        except httpx.HTTPError as e:
            raise NwsUnavailable(f"NWS points lookup failed: {e}") from e
        props = resp.json()["properties"]
        info = {
            "office": props.get("gridId"),
            "grid_x": props.get("gridX"),
            "grid_y": props.get("gridY"),
            "forecast_url": props.get("forecastHourly") or props.get("forecast"),
        }
        self._grid_cache[key] = info
        return info

    def fetch_forecast(self, forecast_url: str) -> tuple[dict[str, Any], bytes]:
        try:
            resp = self._client.get(forecast_url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NwsUnavailable(f"NWS forecast fetch failed: {e}") from e
        return resp.json(), resp.content


_WIND_RE = re.compile(r"(\d+)(?:\s*to\s*(\d+))?\s*mph", re.I)


def _parse_wind(text: str | None) -> float | None:
    """NWS reports wind as text like '10 mph' or '5 to 15 mph'."""
    if not text:
        return None
    m = _WIND_RE.search(text)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    return (lo + hi) / 2.0


def _select_period(periods: list[dict[str, Any]], kickoff_utc: datetime) -> dict[str, Any] | None:
    """The forecast period covering kickoff, else the nearest one."""
    best, best_delta = None, None
    for p in periods:
        start = p.get("startTime")
        end = p.get("endTime")
        if not start:
            continue
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end) if end else s + timedelta(hours=1)
        if s <= kickoff_utc < e:
            return p
        delta = abs((s - kickoff_utc).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = p, delta
    return best


def capture_forecast_for_game(
    session: Session,
    *,
    canonical_game_id: str,
    venue: Venue,
    kickoff_utc: datetime,
    client: NwsClient | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    observed_at: datetime | None = None,
    is_gameday_observation: bool = False,
) -> WeatherForecastVintage | None:
    """Capture one forecast vintage. Returns None when the content is
    identical to the newest existing vintage (no duplicate rows)."""
    observed_at = observed_at or utc_now()
    if venue.country != "US":
        raise VenueNotSupported(
            f"{venue.name} is in {venue.country}; NWS covers US locations only"
        )
    if venue.latitude is None or venue.longitude is None:
        raise VenueNotSupported(f"{venue.name} has no coordinates")

    client = client or NwsClient()
    grid = client.resolve_grid(venue.latitude, venue.longitude)
    if not grid.get("forecast_url"):
        raise VenueNotSupported(f"{venue.name} has no NWS forecast endpoint")

    body, raw = client.fetch_forecast(grid["forecast_url"])
    props = body.get("properties", {})
    periods = props.get("periods", [])
    period = _select_period(periods, kickoff_utc)

    raw_hash = hashlib.sha256(raw).hexdigest()
    newest = session.scalars(
        select(WeatherForecastVintage)
        .where(
            WeatherForecastVintage.canonical_game_id == canonical_game_id,
            WeatherForecastVintage.data_mode == data_mode.value,
        )
        .order_by(WeatherForecastVintage.observed_at.desc())
        .limit(1)
    ).first()
    if newest is not None and newest.raw_hash == raw_hash:
        return None

    settings.ensure_dirs()
    wx_dir = settings.data_dir / "weather"
    wx_dir.mkdir(parents=True, exist_ok=True)
    artifact = wx_dir / f"{canonical_game_id}_{observed_at:%Y%m%dT%H%M%S}_{raw_hash[:8]}.json"
    artifact.write_bytes(raw)

    updated = props.get("updated") or props.get("updateTime")
    source_updated = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None

    temp = period.get("temperature") if period else None
    if period and (period.get("temperatureUnit") or "F").upper() == "C" and temp is not None:
        temp = temp * 9 / 5 + 32

    rh = (period or {}).get("relativeHumidity") or {}
    pop = (period or {}).get("probabilityOfPrecipitation") or {}

    vintage = WeatherForecastVintage(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        provider="nws",
        office=grid.get("office"),
        grid_x=grid.get("grid_x"),
        grid_y=grid.get("grid_y"),
        forecast_period_start=datetime.fromisoformat(period["startTime"]) if period and period.get("startTime") else None,
        forecast_period_end=datetime.fromisoformat(period["endTime"]) if period and period.get("endTime") else None,
        temp_f=float(temp) if temp is not None else None,
        wind_mph=_parse_wind((period or {}).get("windSpeed")),
        wind_gust_mph=_parse_wind((period or {}).get("windGust")),
        precip_probability=(float(pop["value"]) / 100.0 if pop.get("value") is not None else None),
        precip_description=(period or {}).get("shortForecast"),
        humidity=(float(rh["value"]) if rh.get("value") is not None else None),
        narrative=(period or {}).get("detailedForecast"),
        source_updated_at=source_updated,
        observed_at=observed_at,
        raw_hash=raw_hash,
        raw_artifact_path=str(artifact),
        is_gameday_observation=is_gameday_observation,
    )
    session.add(vintage)
    session.flush()
    return vintage


def capture_forecasts_for_slate(
    session: Session,
    games: list[Any],
    *,
    client: NwsClient | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    max_days_ahead: int = 7,
) -> WeatherCaptureResult:
    """Capture forecasts for games within the NWS forecast horizon.

    NWS forecasts extend ~7 days; asking earlier yields nothing useful, so
    distant games are skipped rather than recorded as failures.
    """
    res = WeatherCaptureResult()
    client = client or NwsClient()
    now = utc_now()
    for g in games:
        if g.kickoff_utc is None:
            continue
        if (g.kickoff_utc - now) > timedelta(days=max_days_ahead):
            continue
        venue = session.get(Venue, g.stadium_id) if g.stadium_id else None
        if venue is None:
            res.unsupported += 1
            res.details.append(f"{g.canonical_game_id}: no governed venue")
            continue
        if not venue.weather_applicable:
            res.not_applicable += 1
            continue
        res.attempted += 1
        try:
            v = capture_forecast_for_game(
                session, canonical_game_id=g.canonical_game_id, venue=venue,
                kickoff_utc=g.kickoff_utc, client=client, data_mode=data_mode,
            )
            if v is None:
                res.unchanged += 1
            else:
                res.captured += 1
        except VenueNotSupported as e:
            res.unsupported += 1
            res.details.append(f"{g.canonical_game_id}: {e}")
        except NwsUnavailable as e:
            res.failed += 1
            res.details.append(f"{g.canonical_game_id}: {e}")
    return res


def forecast_as_of(
    session: Session,
    *,
    canonical_game_id: str,
    as_of_at: datetime,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    allow_gameday_observation: bool = False,
) -> WeatherForecastVintage | None:
    """The newest forecast vintage observable at a cutoff.

    Game-day *observations* are excluded from prediction inputs by default;
    they exist for evaluation only and would be lookahead in a forecast.
    """
    stmt = select(WeatherForecastVintage).where(
        WeatherForecastVintage.canonical_game_id == canonical_game_id,
        WeatherForecastVintage.data_mode == data_mode.value,
        WeatherForecastVintage.observed_at <= as_of_at,
    )
    if not allow_gameday_observation:
        stmt = stmt.where(WeatherForecastVintage.is_gameday_observation.is_(False))
    return session.scalars(
        stmt.order_by(WeatherForecastVintage.observed_at.desc(), WeatherForecastVintage.id.desc()).limit(1)
    ).first()


def record_roof_state(
    session: Session,
    *,
    canonical_game_id: str,
    state: str,
    source_category: str,
    source_reference: str | None = None,
    observed_at: datetime | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> RoofStateObservation:
    """Append a roof-state observation.

    Never inferred from the result or a postgame report — an unobserved
    roof stays UNKNOWN, which the policy turns into DATA INCOMPLETE for
    weather-sensitive venues.
    """
    valid = {"OPEN", "CLOSED", "EXPECTED_OPEN", "EXPECTED_CLOSED", "UNKNOWN", "NOT_APPLICABLE"}
    if state not in valid:
        raise ValueError(f"Invalid roof state {state!r}; expected one of {sorted(valid)}")
    now = utc_now()
    prev = session.scalars(
        select(RoofStateObservation)
        .where(
            RoofStateObservation.canonical_game_id == canonical_game_id,
            RoofStateObservation.data_mode == data_mode.value,
        )
        .order_by(RoofStateObservation.observed_at.desc())
        .limit(1)
    ).first()
    obs = RoofStateObservation(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        state=state,
        source_category=source_category,
        source_reference=source_reference,
        observed_at=observed_at or now,
        entered_at=now,
    )
    session.add(obs)
    session.flush()
    if prev is not None:
        prev.superseded_by_id = obs.id
    return obs


def roof_state_as_of(
    session: Session,
    *,
    canonical_game_id: str,
    as_of_at: datetime,
    venue: Venue | None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> str:
    """Roof state known at a cutoff, defaulting conservatively."""
    if venue is not None and venue.roof_type == "OUTDOOR":
        return "NOT_APPLICABLE"
    if venue is not None and venue.roof_type == "DOME":
        return "CLOSED"
    obs = session.scalars(
        select(RoofStateObservation)
        .where(
            RoofStateObservation.canonical_game_id == canonical_game_id,
            RoofStateObservation.data_mode == data_mode.value,
            RoofStateObservation.observed_at <= as_of_at,
        )
        .order_by(RoofStateObservation.observed_at.desc())
        .limit(1)
    ).first()
    return obs.state if obs else "UNKNOWN"


def weather_snapshot_dict(v: WeatherForecastVintage | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {
        "vintage_id": v.id,
        "office": v.office,
        "grid": [v.grid_x, v.grid_y],
        "temp_f": v.temp_f,
        "wind_mph": v.wind_mph,
        "wind_gust_mph": v.wind_gust_mph,
        "precip_probability": v.precip_probability,
        "precip_description": v.precip_description,
        "humidity": v.humidity,
        "source_updated_at": v.source_updated_at.isoformat() if v.source_updated_at else None,
        "observed_at": v.observed_at.isoformat(),
        "raw_hash": v.raw_hash,
    }


def _fingerprint(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
