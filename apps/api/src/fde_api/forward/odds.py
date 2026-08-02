"""Prospective market capture via a permitted odds provider.

Implemented against The Odds API, selected through environment
configuration. The provider is behind an adapter so a different licensed
feed is a new class, not a rewrite.

Hard rules:
  * Prices are recorded exactly as received, with both the provider's
    timestamp and our local observation instant.
  * Nothing is fabricated or interpolated. A gap in coverage stays a gap.
  * Live (in-play) quotes are flagged and excluded from consensus.
  * bet365 is never contacted here; the user's executable price arrives
    only through manual entry.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.canonical.team_map import canonical_team_code
from fde_api.db.forward_models import OddsQuote, ProviderQuotaUsage
from fde_api.forward.cohort import ProviderMode, assert_capturable
from fde_api.forward.modes import DataMode
from fde_api.util import utc_now

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_nfl"

# Provider market key -> canonical market.
_MARKET_MAP = {"h2h": "MONEYLINE", "spreads": "SPREAD", "totals": "TOTAL"}


class OddsProviderNotConfigured(RuntimeError):
    """No provider credential is configured.

    Raised rather than silently returning nothing, so a missing key can
    never be mistaken for "the market has no prices".
    """


def american_to_decimal(american: int) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / -american)


def decimal_to_american(dec: float) -> int:
    if dec >= 2.0:
        return round((dec - 1.0) * 100)
    return round(-100.0 / (dec - 1.0))


@dataclass
class PollCadence:
    """Research polling cadence, expressed as (hours-before-kickoff,
    interval-minutes). Configurable; quota is enforced separately."""

    tiers: tuple[tuple[float, int], ...] = (
        (float("inf"), 360),  # > 7 days  -> every 6 hours
        (168.0, 60),          # 7d - 24h  -> hourly
        (24.0, 15),           # 24h - 6h  -> every 15 min
        (6.0, 5),             # 6h - 90m  -> every 5 min
        (1.5, 2),             # final 90m -> every 2 min (quota permitting)
    )
    closing_capture_minutes_before: int = 10

    def interval_minutes(self, hours_to_kickoff: float) -> int:
        chosen = self.tiers[0][1]
        for threshold, minutes in self.tiers:
            if hours_to_kickoff <= threshold:
                chosen = minutes
        return chosen

    def due(self, hours_to_kickoff: float, last_poll: datetime | None, now: datetime) -> bool:
        if last_poll is None:
            return True
        return now - last_poll >= timedelta(minutes=self.interval_minutes(hours_to_kickoff))


@dataclass
class OddsCaptureResult:
    provider: str
    events_seen: int = 0
    quotes_received: int = 0
    quotes_written: int = 0
    duplicates_skipped: int = 0
    live_quotes_skipped: int = 0
    invalid_skipped: int = 0
    unmapped_events: list[str] = field(default_factory=list)
    quota_remaining: int | None = None
    request_id: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "events_seen": self.events_seen,
            "quotes_received": self.quotes_received,
            "quotes_written": self.quotes_written,
            "duplicates_skipped": self.duplicates_skipped,
            "live_quotes_skipped": self.live_quotes_skipped,
            "invalid_skipped": self.invalid_skipped,
            "unmapped_events": self.unmapped_events,
            "quota_remaining": self.quota_remaining,
            "request_id": self.request_id,
            "warnings": self.warnings,
        }


class TheOddsApiProvider:
    """Adapter for The Odds API. The key is read from the backend
    environment only and never reaches the browser bundle."""

    name = "the-odds-api"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self.api_key = api_key or os.environ.get("FDE_ODDS_API_KEY") or ""
        self._client = client or httpx.Client(timeout=45.0, follow_redirects=True)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch_odds(
        self,
        *,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        odds_format: str = "american",
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not self.configured:
            raise OddsProviderNotConfigured(
                "FDE_ODDS_API_KEY is not set. Live market capture requires a provider key; "
                "refusing to proceed so that 'no key' is never mistaken for 'no prices'."
            )
        resp = self._client.get(
            f"{THE_ODDS_API_BASE}/sports/{SPORT_KEY}/odds",
            params={
                "apiKey": self.api_key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": odds_format,
                "dateFormat": "iso",
            },
        )
        resp.raise_for_status()
        return resp.json(), dict(resp.headers)


def _match_canonical_game(
    session: Session,
    *,
    home_team: str,
    away_team: str,
    commence_time: datetime,
    data_mode: DataMode,
) -> str | None:
    """Map a provider event to a canonical game id.

    Matched on (home, away, kickoff within +/-36h) against the observed
    schedule — never on team display names alone.
    """
    from fde_api.db.forward_models import ScheduleObservation

    try:
        home = canonical_team_code(home_team)
        away = canonical_team_code(away_team)
    except KeyError:
        return None
    lo, hi = commence_time - timedelta(hours=36), commence_time + timedelta(hours=36)
    rows = session.scalars(
        select(ScheduleObservation)
        .where(
            ScheduleObservation.data_mode == data_mode.value,
            ScheduleObservation.home_team_id == home,
            ScheduleObservation.away_team_id == away,
            ScheduleObservation.kickoff_utc.isnot(None),
            ScheduleObservation.kickoff_utc >= lo,
            ScheduleObservation.kickoff_utc <= hi,
        )
        .order_by(ScheduleObservation.observed_at.desc())
        .limit(1)
    ).first()
    return rows.canonical_game_id if rows else None


# Team-name aliases used by odds providers -> canonical code.
_PROVIDER_TEAM_ALIASES = {
    "arizona cardinals": "ARI", "atlanta falcons": "ATL", "baltimore ravens": "BAL",
    "buffalo bills": "BUF", "carolina panthers": "CAR", "chicago bears": "CHI",
    "cincinnati bengals": "CIN", "cleveland browns": "CLE", "dallas cowboys": "DAL",
    "denver broncos": "DEN", "detroit lions": "DET", "green bay packers": "GB",
    "houston texans": "HOU", "indianapolis colts": "IND", "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC", "las vegas raiders": "LV", "los angeles chargers": "LAC",
    "los angeles rams": "LA", "miami dolphins": "MIA", "minnesota vikings": "MIN",
    "new england patriots": "NE", "new orleans saints": "NO", "new york giants": "NYG",
    "new york jets": "NYJ", "philadelphia eagles": "PHI", "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF", "seattle seahawks": "SEA", "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN", "washington commanders": "WAS",
}


def provider_team_to_code(name: str) -> str:
    key = (name or "").strip().lower()
    if key in _PROVIDER_TEAM_ALIASES:
        return _PROVIDER_TEAM_ALIASES[key]
    raise KeyError(f"Unmapped provider team name {name!r}")


def capture_odds(
    session: Session,
    payload: list[dict[str, Any]],
    *,
    provider: str = "the-odds-api",
    request_id: str | None = None,
    quota_remaining: int | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    provider_mode: ProviderMode,
    observed_at: datetime | None = None,
) -> OddsCaptureResult:
    """Persist a provider payload as immutable timestamped quotes.

    Idempotent: re-processing the same payload writes nothing new, because
    a quote's identity is (game, book, market, selection, line, price,
    provider timestamp).

    `provider_mode` records where the payload came from and is stored on
    every quote. It has no default on purpose: a fixture payload and a live
    one are structurally identical, so only the caller knows, and a default
    of LIVE would silently mislabel every caller that forgot.
    """
    assert_capturable(provider_mode)
    observed_at = observed_at or utc_now()
    res = OddsCaptureResult(provider=provider, request_id=request_id, quota_remaining=quota_remaining)

    for event in payload:
        res.events_seen += 1
        try:
            home_code = provider_team_to_code(event.get("home_team", ""))
            away_code = provider_team_to_code(event.get("away_team", ""))
        except KeyError as e:
            res.unmapped_events.append(str(e))
            continue
        commence = event.get("commence_time")
        commence_dt = datetime.fromisoformat(commence.replace("Z", "+00:00")) if commence else None
        if commence_dt is None:
            res.warnings.append(f"event {event.get('id')} has no commence_time; skipped")
            continue

        game_id = _match_canonical_game(
            session, home_team=home_code, away_team=away_code,
            commence_time=commence_dt, data_mode=data_mode,
        )
        if game_id is None:
            res.unmapped_events.append(f"{away_code}@{home_code} {commence_dt.isoformat()}")
            continue

        for book in event.get("bookmakers", []):
            book_key = book.get("key") or "unknown"
            book_updated = book.get("last_update")
            book_ts = datetime.fromisoformat(book_updated.replace("Z", "+00:00")) if book_updated else None
            for market in book.get("markets", []):
                canonical_market = _MARKET_MAP.get(market.get("key", ""))
                if canonical_market is None:
                    continue
                for outcome in market.get("outcomes", []):
                    res.quotes_received += 1
                    written = _write_quote(
                        session,
                        game_id=game_id,
                        home_code=home_code,
                        provider=provider,
                        provider_mode=provider_mode,
                        provider_event_id=event.get("id"),
                        sportsbook=book_key,
                        market=canonical_market,
                        outcome=outcome,
                        provider_timestamp=book_ts,
                        observed_at=observed_at,
                        request_id=request_id,
                        data_mode=data_mode,
                        result=res,
                    )
                    if written:
                        res.quotes_written += 1

    if quota_remaining is not None:
        session.add(
            ProviderQuotaUsage(
                provider=provider,
                window_start=observed_at,
                calls_used=1,
                calls_remaining=quota_remaining,
                quota_limit=None,
                last_response_at=observed_at,
            )
        )
    session.flush()
    return res


def _write_quote(
    session: Session,
    *,
    game_id: str,
    home_code: str,
    provider: str,
    provider_mode: ProviderMode,
    provider_event_id: str | None,
    sportsbook: str,
    market: str,
    outcome: dict[str, Any],
    provider_timestamp: datetime | None,
    observed_at: datetime,
    request_id: str | None,
    data_mode: DataMode,
    result: OddsCaptureResult,
) -> bool:
    name = (outcome.get("name") or "").strip()
    price = outcome.get("price")
    point = outcome.get("point")

    if market == "TOTAL":
        selection = "OVER" if name.lower() == "over" else "UNDER" if name.lower() == "under" else None
    else:
        try:
            code = provider_team_to_code(name)
        except KeyError:
            result.invalid_skipped += 1
            return False
        selection = "HOME" if code == home_code else "AWAY"
    if selection is None or price is None:
        result.invalid_skipped += 1
        return False

    try:
        american = int(price)
    except (TypeError, ValueError):
        result.invalid_skipped += 1
        return False
    if abs(american) < 100 or abs(american) > 100_000:
        result.invalid_skipped += 1
        return False

    line = float(point) if point is not None else None
    if market == "SPREAD" and line is None:
        result.invalid_skipped += 1
        return False
    if line is not None and abs(line) > 100:
        result.invalid_skipped += 1
        return False
    # nflverse/bookmaker convention: store SPREAD line home-relative.
    if market == "SPREAD" and selection == "AWAY" and line is not None:
        line = -line

    raw_hash = hashlib.sha256(
        json.dumps(
            {"g": game_id, "b": sportsbook, "m": market, "s": selection,
             "l": line, "p": american, "t": provider_timestamp.isoformat() if provider_timestamp else None},
            sort_keys=True,
        ).encode()
    ).hexdigest()

    exists = session.scalars(
        select(OddsQuote).where(
            OddsQuote.canonical_game_id == game_id,
            OddsQuote.sportsbook == sportsbook,
            OddsQuote.market == market,
            OddsQuote.selection == selection,
            OddsQuote.american == american,
            OddsQuote.raw_hash == raw_hash,
        ).limit(1)
    ).first()
    if exists is not None:
        result.duplicates_skipped += 1
        return False

    session.add(
        OddsQuote(
            data_mode=data_mode.value,
            canonical_game_id=game_id,
            provider=provider,
            provider_mode=provider_mode.value,
            provider_event_id=provider_event_id,
            sportsbook=sportsbook,
            market=market,
            selection=selection,
            line=line,
            american=american,
            decimal_odds=american_to_decimal(american),
            is_live=False,
            provider_timestamp=provider_timestamp,
            observed_at=observed_at,
            request_id=request_id,
            raw_hash=raw_hash,
        )
    )
    return True
