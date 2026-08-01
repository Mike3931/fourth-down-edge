"""Governed venue reference.

Coordinates, time zone, roof type, surface, and elevation for every
venue the 2026 schedule can use, plus the international sites. Roof type
decides whether weather forecasts are even applicable; international
venues are marked non-US so the NWS adapter fails cleanly rather than
silently producing nothing.

`verification_status` is explicit: VERIFIED entries were checked against
the published venue; UNVERIFIED entries are usable but flagged in Data
Health so nobody mistakes an assumption for a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import Venue
from fde_api.util import utc_now


@dataclass(frozen=True)
class VenueSpec:
    id: str
    name: str
    lat: float | None
    lon: float | None
    tz: str | None
    roof_type: str  # OUTDOOR | DOME | RETRACTABLE | UNKNOWN
    surface: str | None
    elevation_ft: float | None
    country: str = "US"
    verified: bool = True

    @property
    def weather_applicable(self) -> bool:
        # A fixed dome never needs a forecast. Retractable roofs do, because
        # the roof state is itself uncertain until observed.
        return self.roof_type != "DOME"


# nflverse stadium_id -> venue. Elevations are approximate field elevation.
#
# WARNING about neutral-site games: the source populates `stadium_id` (and
# `roof`) with the HOME CLUB's stadium even when the game is played abroad —
# e.g. 2026_01_SF_LA at Melbourne Cricket Ground carries LAX01/"dome". Those
# columns are therefore NOT trustworthy for neutral games, and
# `resolve_venue()` below ignores them in favour of a name lookup.
VENUES: tuple[VenueSpec, ...] = (
    VenueSpec("PHO00", "State Farm Stadium", 33.5276, -112.2626, "America/Phoenix", "RETRACTABLE", "grass", 1070),
    VenueSpec("ATL97", "Mercedes-Benz Stadium", 33.7554, -84.4010, "America/New_York", "RETRACTABLE", "fieldturf", 1050),
    VenueSpec("BAL00", "M&T Bank Stadium", 39.2780, -76.6227, "America/New_York", "OUTDOOR", "grass", 33),
    VenueSpec("BUF00", "Highmark Stadium", 42.7738, -78.7870, "America/New_York", "OUTDOOR", "a_turf", 600),
    VenueSpec("CAR00", "Bank of America Stadium", 35.2258, -80.8528, "America/New_York", "OUTDOOR", "grass", 751),
    VenueSpec("CHI98", "Soldier Field", 41.8623, -87.6167, "America/Chicago", "OUTDOOR", "grass", 597),
    VenueSpec("CIN00", "Paycor Stadium", 39.0955, -84.5161, "America/New_York", "OUTDOOR", "fieldturf", 490),
    VenueSpec("CLE00", "Huntington Bank Field", 41.5061, -81.6995, "America/New_York", "OUTDOOR", "grass", 581),
    VenueSpec("DAL00", "AT&T Stadium", 32.7473, -97.0945, "America/Chicago", "RETRACTABLE", "matrixturf", 551),
    VenueSpec("DEN00", "Empower Field at Mile High", 39.7439, -105.0201, "America/Denver", "OUTDOOR", "grass", 5280),
    VenueSpec("DET00", "Ford Field", 42.3400, -83.0456, "America/Detroit", "DOME", "fieldturf", 600),
    VenueSpec("GNB00", "Lambeau Field", 44.5013, -88.0622, "America/Chicago", "OUTDOOR", "grass", 640),
    VenueSpec("HOU00", "NRG Stadium", 29.6847, -95.4107, "America/Chicago", "RETRACTABLE", "grass", 49),
    VenueSpec("IND00", "Lucas Oil Stadium", 39.7601, -86.1639, "America/Indiana/Indianapolis", "RETRACTABLE", "fieldturf", 715),
    VenueSpec("JAX00", "EverBank Stadium", 30.3239, -81.6373, "America/New_York", "OUTDOOR", "grass", 16),
    VenueSpec("KAN00", "GEHA Field at Arrowhead", 39.0489, -94.4839, "America/Chicago", "OUTDOOR", "grass", 889),
    VenueSpec("LAX01", "SoFi Stadium", 33.9535, -118.3392, "America/Los_Angeles", "DOME", "matrixturf", 82),
    VenueSpec("LAX97", "SoFi Stadium", 33.9535, -118.3392, "America/Los_Angeles", "DOME", "matrixturf", 82),
    VenueSpec("VEG00", "Allegiant Stadium", 36.0909, -115.1833, "America/Los_Angeles", "DOME", "grass", 2030),
    VenueSpec("MIA00", "Hard Rock Stadium", 25.9580, -80.2389, "America/New_York", "OUTDOOR", "grass", 8),
    VenueSpec("MIN01", "U.S. Bank Stadium", 44.9735, -93.2575, "America/Chicago", "DOME", "sportturf", 840),
    VenueSpec("BOS00", "Gillette Stadium", 42.0909, -71.2643, "America/New_York", "OUTDOOR", "fieldturf", 289),
    VenueSpec("NOR00", "Caesars Superdome", 29.9511, -90.0812, "America/Chicago", "DOME", "fieldturf", 3),
    VenueSpec("NYC01", "MetLife Stadium", 40.8135, -74.0745, "America/New_York", "OUTDOOR", "fieldturf", 20),
    VenueSpec("PHI00", "Lincoln Financial Field", 39.9008, -75.1675, "America/New_York", "OUTDOOR", "grass", 39),
    VenueSpec("PIT00", "Acrisure Stadium", 40.4468, -80.0158, "America/New_York", "OUTDOOR", "grass", 730),
    VenueSpec("SEA00", "Lumen Field", 47.5952, -122.3316, "America/Los_Angeles", "OUTDOOR", "fieldturf", 20),
    VenueSpec("SFO01", "Levi's Stadium", 37.4030, -121.9700, "America/Los_Angeles", "OUTDOOR", "grass", 26),
    VenueSpec("TAM00", "Raymond James Stadium", 27.9759, -82.5033, "America/New_York", "OUTDOOR", "grass", 26),
    VenueSpec("NAS00", "Nissan Stadium", 36.1665, -86.7713, "America/Chicago", "OUTDOOR", "grass", 385),
    VenueSpec("WAS00", "Northwest Stadium", 38.9077, -76.8645, "America/New_York", "OUTDOOR", "grass", 200),
)


# Neutral-site venues are keyed by NORMALIZED STADIUM NAME, because the
# source does not give them their own stadium_id.
NEUTRAL_VENUES: tuple[VenueSpec, ...] = (
    VenueSpec("INT_WEMBLEY", "Wembley Stadium", 51.5560, -0.2795, "Europe/London", "OUTDOOR", "grass", 130, "GB"),
    VenueSpec("INT_TOTTENHAM", "Tottenham Hotspur Stadium", 51.6043, -0.0665, "Europe/London", "OUTDOOR", "grass", 100, "GB"),
    VenueSpec("INT_AZTECA", "Estadio Banorte", 19.3029, -99.1505, "America/Mexico_City", "OUTDOOR", "grass", 7280, "MX"),
    VenueSpec("INT_ALLIANZ", "FC Bayern Munich Stadium", 48.2188, 11.6247, "Europe/Berlin", "OUTDOOR", "grass", 1690, "DE"),
    VenueSpec("INT_BERNABEU", "Bernabeu", 40.4531, -3.6883, "Europe/Madrid", "RETRACTABLE", "grass", 2130, "ES"),
    VenueSpec("INT_MARACANA", "Maracana Stadium", -22.9121, -43.2302, "America/Sao_Paulo", "OUTDOOR", "grass", 30, "BR"),
    VenueSpec("INT_STADE_FRANCE", "Stade de France", 48.9245, 2.3601, "Europe/Paris", "OUTDOOR", "grass", 100, "FR"),
    VenueSpec("INT_MCG", "Melbourne Cricket Ground", -37.8200, 144.9834, "Australia/Melbourne", "OUTDOOR", "grass", 100, "AU"),
    VenueSpec("INT_CROKE", "Croke Park", 53.3607, -6.2512, "Europe/Dublin", "OUTDOOR", "grass", 60, "IE"),
)


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


NEUTRAL_BY_NAME = {_normalize_name(v.name): v for v in NEUTRAL_VENUES}

VENUES_BY_ID = {v.id: v for v in (*VENUES, *NEUTRAL_VENUES)}

US_COUNTRIES = {"US"}
INTERNATIONAL_COUNTRIES = {v.country for v in NEUTRAL_VENUES} - US_COUNTRIES


def seed_venues(session: Session) -> int:
    """Idempotently upsert the governed venue table."""
    n = 0
    for v in (*VENUES, *NEUTRAL_VENUES):
        session.merge(
            Venue(
                id=v.id,
                name=v.name,
                latitude=v.lat,
                longitude=v.lon,
                timezone=v.tz,
                roof_type=v.roof_type,
                surface=v.surface,
                elevation_ft=v.elevation_ft,
                weather_applicable=v.weather_applicable,
                country=v.country,
                source="fde governed venue reference v1",
                verification_status="VERIFIED" if v.verified else "UNVERIFIED",
                updated_at=utc_now(),
            )
        )
        n += 1
    session.flush()
    return n


def get_venue(session: Session, stadium_id: str | None) -> Venue | None:
    if not stadium_id:
        return None
    return session.get(Venue, stadium_id)


def unmapped_stadium_ids(session: Session, stadium_ids: set[str]) -> list[str]:
    """Stadium ids present in the schedule but absent from the governed
    table — surfaced by Data Health rather than silently defaulted."""
    known = set(session.scalars(select(Venue.id)))
    return sorted(s for s in stadium_ids if s and s not in known)


def resolve_venue(*, stadium_id: str | None, stadium_name: str | None, neutral_site: bool) -> VenueSpec | None:
    """Resolve the venue a game is actually played at.

    Resolution is by VENUE NAME first, never by the `location` flag.

    Two independent source quirks make the flag unusable:
      * For games played abroad the source populates `stadium_id` and
        `roof` with the HOME CLUB's usual stadium (2026_01_SF_LA at
        Melbourne Cricket Ground carries LAX01/"dome").
      * A club's designated home game may still be played abroad, in
        which case `location` reads "Home" even though the venue is
        international (2026_05_PHI_JAX at Tottenham Hotspur Stadium).

    Keying off the flag therefore silently sends an international game to
    a domestic stadium — wrong coordinates, wrong time zone, wrong roof.
    Matching the name against the international registry first is
    unambiguous, because that registry contains only overseas venues.
    """
    by_name = NEUTRAL_BY_NAME.get(_normalize_name(stadium_name or ""))
    if by_name is not None:
        return by_name
    if neutral_site:
        # Declared neutral but the venue is unknown: refuse to fall back to
        # the home club's stadium, which would be actively misleading.
        return None
    return VENUES_BY_ID.get(stadium_id or "")


def is_international(venue: VenueSpec | None) -> bool:
    return bool(venue and venue.country in INTERNATIONAL_COUNTRIES)
