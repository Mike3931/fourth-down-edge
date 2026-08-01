"""Static geographic reference for travel features.

Home-stadium coordinates and time zones per franchise (current era).
This is fixed public reference data, versioned with the code; games at
neutral/international sites fall back to the listed stadium of the home
club and are flagged `neutral_site_uncertain` by the builder when the
schedule marks a neutral location.
"""

from __future__ import annotations

from math import atan2, cos, radians, sin, sqrt

# team -> (lat, lon, IANA tz)
TEAM_HOME: dict[str, tuple[float, float, str]] = {
    "ARI": (33.5276, -112.2626, "America/Phoenix"),
    "ATL": (33.7554, -84.4010, "America/New_York"),
    "BAL": (39.2780, -76.6227, "America/New_York"),
    "BUF": (42.7738, -78.7870, "America/New_York"),
    "CAR": (35.2258, -80.8528, "America/New_York"),
    "CHI": (41.8623, -87.6167, "America/Chicago"),
    "CIN": (39.0955, -84.5161, "America/New_York"),
    "CLE": (41.5061, -81.6995, "America/New_York"),
    "DAL": (32.7473, -97.0945, "America/Chicago"),
    "DEN": (39.7439, -105.0201, "America/Denver"),
    "DET": (42.3400, -83.0456, "America/Detroit"),
    "GB": (44.5013, -88.0622, "America/Chicago"),
    "HOU": (29.6847, -95.4107, "America/Chicago"),
    "IND": (39.7601, -86.1639, "America/Indiana/Indianapolis"),
    "JAX": (30.3239, -81.6373, "America/New_York"),
    "KC": (39.0489, -94.4839, "America/Chicago"),
    "LA": (33.9535, -118.3392, "America/Los_Angeles"),
    "LAC": (33.9535, -118.3392, "America/Los_Angeles"),
    "LV": (36.0909, -115.1833, "America/Los_Angeles"),
    "MIA": (25.9580, -80.2389, "America/New_York"),
    "MIN": (44.9735, -93.2575, "America/Chicago"),
    "NE": (42.0909, -71.2643, "America/New_York"),
    "NO": (29.9511, -90.0812, "America/Chicago"),
    "NYG": (40.8135, -74.0745, "America/New_York"),
    "NYJ": (40.8135, -74.0745, "America/New_York"),
    "PHI": (39.9008, -75.1675, "America/New_York"),
    "PIT": (40.4468, -80.0158, "America/New_York"),
    "SEA": (47.5952, -122.3316, "America/Los_Angeles"),
    "SF": (37.4030, -121.9700, "America/Los_Angeles"),
    "TB": (27.9759, -82.5033, "America/New_York"),
    "TEN": (36.1665, -86.7713, "America/Chicago"),
    "WAS": (38.9077, -76.8645, "America/New_York"),
}

_TZ_OFFSET_HOURS: dict[str, int] = {
    "America/New_York": -5,
    "America/Detroit": -5,
    "America/Chicago": -6,
    "America/Indiana/Indianapolis": -5,
    "America/Denver": -7,
    "America/Phoenix": -7,
    "America/Los_Angeles": -8,
}


def travel_miles(from_team: str, to_team: str) -> float | None:
    a, b = TEAM_HOME.get(from_team), TEAM_HOME.get(to_team)
    if a is None or b is None:
        return None
    lat1, lon1, _ = a
    lat2, lon2, _ = b
    r = 3958.8
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * atan2(sqrt(h), sqrt(1 - h))


def tz_change_hours(from_team: str, to_team: str) -> int | None:
    a, b = TEAM_HOME.get(from_team), TEAM_HOME.get(to_team)
    if a is None or b is None:
        return None
    return _TZ_OFFSET_HOURS[b[2]] - _TZ_OFFSET_HOURS[a[2]]
