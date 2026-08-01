"""Franchise identity across relocations/renames.

nflverse uses era-specific abbreviations; the canonical model keys every
franchise by its current abbreviation and records the historical codes
in provider_entity_map. Mapping is by code — never by display name.
"""

from __future__ import annotations

# historical nflverse code -> canonical (current) code
LEGACY_TEAM_CODES: dict[str, str] = {
    "SD": "LAC",
    "OAK": "LV",
    "STL": "LA",
}

CANONICAL_TEAMS: dict[str, str] = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "LA": "Los Angeles Rams",
    "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WAS": "Washington Commanders",
}


def canonical_team_code(provider_code: str) -> str:
    code = provider_code.strip().upper()
    code = LEGACY_TEAM_CODES.get(code, code)
    if code not in CANONICAL_TEAMS:
        raise KeyError(f"Unknown team code {provider_code!r}")
    return code
