"""Canonical loader validations against corrupted source data."""

from __future__ import annotations

import pytest

from fde_api.canonical import load_games_csv
from fde_api.canonical.team_map import canonical_team_code

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,"
    "home_team,home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,"
    "pfr,pff,espn,ftn,away_rest,home_rest,away_moneyline,home_moneyline,spread_line,"
    "away_spread_odds,home_spread_odds,total_line,under_odds,over_odds,div_game,roof,"
    "surface,temp,wind,away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,"
    "home_coach,referee,stadium_id,stadium"
)


def _row(**over: str) -> str:
    base = {
        "game_id": "2025_01_KC_LAC", "season": "2025", "game_type": "REG", "week": "1",
        "gameday": "2025-09-07", "weekday": "Sunday", "gametime": "13:00",
        "away_team": "KC", "away_score": "21", "home_team": "LAC", "home_score": "24",
        "location": "Home", "result": "3", "total": "45", "overtime": "0",
        "old_game_id": "", "gsis": "", "nfl_detail_id": "", "pfr": "", "pff": "", "espn": "", "ftn": "",
        "away_rest": "7", "home_rest": "7", "away_moneyline": "120", "home_moneyline": "-140",
        "spread_line": "2.5", "away_spread_odds": "-110", "home_spread_odds": "-110",
        "total_line": "44.5", "under_odds": "-110", "over_odds": "-110", "div_game": "1",
        "roof": "outdoors", "surface": "grass", "temp": "72", "wind": "5",
        "away_qb_id": "00-001", "home_qb_id": "00-002", "away_qb_name": "A", "home_qb_name": "B",
        "away_coach": "CA", "home_coach": "CB", "referee": "Ref Name", "stadium_id": "STD1",
        "stadium": "Stadium One",
    }
    base.update(over)
    return ",".join(base[k] for k in HEADER.split(","))


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def test_duplicate_game_ids_rejected(session) -> None:
    payload = _csv(_row(), _row())
    with pytest.raises(ValueError, match="Duplicate game_id"):
        load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")


def test_unknown_team_rejected(session) -> None:
    payload = _csv(_row(away_team="XXX"))
    with pytest.raises(ValueError, match="unknown team"):
        load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")


def test_identical_home_away_rejected(session) -> None:
    payload = _csv(_row(away_team="LAC"))
    with pytest.raises(ValueError, match="identical"):
        load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")


def test_impossible_score_rejected(session) -> None:
    payload = _csv(_row(home_score="212"))
    with pytest.raises(ValueError, match="impossible"):
        load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")


def test_missing_kickoff_excluded_with_quality_event(session) -> None:
    payload = _csv(_row(gameday=""))
    res = load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")
    assert res.games_loaded == 0 and res.games_skipped == 1
    assert any("missing kickoff" in e for e in res.quality_events)


def test_invalid_odds_drop_market_with_quality_event(session) -> None:
    payload = _csv(_row(home_moneyline="-5"))
    res = load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")
    assert res.games_loaded == 1
    assert res.odds_rows == 0
    assert any("invalid american price" in e for e in res.quality_events)


def test_valid_row_loads_odds_and_flips_spread_sign(session) -> None:
    payload = _csv(_row())
    res = load_games_csv(session, payload, seasons=[2025], source_manifest_version="t")
    assert res.games_loaded == 1 and res.odds_rows == 3
    from sqlalchemy import select

    from fde_api.db.models import OddsSnapshot

    spread = session.scalars(select(OddsSnapshot).where(OddsSnapshot.market == "SPREAD")).one()
    # nflverse spread_line 2.5 (home favored) → bookmaker home handicap −2.5
    assert spread.line == -2.5
    assert spread.snapshot_kind == "CLOSING_BENCHMARK"


def test_legacy_team_codes_map_by_id() -> None:
    assert canonical_team_code("SD") == "LAC"
    assert canonical_team_code("OAK") == "LV"
    assert canonical_team_code("STL") == "LA"
    with pytest.raises(KeyError):
        canonical_team_code("NOPE")
