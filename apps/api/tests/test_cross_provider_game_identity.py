from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from fde_api.db.forward_models import (
    GameIdentityReview,
    OddsQuote,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.forward.modes import DataMode
from fde_api.forward.results import result_scores
from fde_api.forward.schedule import ingest_schedule
from test_espn_capture import PRICES, _game

pytest_plugins = ["test_espn_capture"]


def nflverse(s, game, *, source_id="2026_00_CAR_ARI", when=None):
    local = game["kickoff_utc"].astimezone(ZoneInfo("America/New_York"))
    payload = ("game_id,season,game_type,week,home_team,away_team,gameday,gametime,stadium\n"
               f"{source_id},2026,PRE,0,ARI,CAR,{local:%Y-%m-%d},{local:%H:%M},"
               "Tom Benson Hall of Fame Stadium\n").encode()
    return ingest_schedule(s, payload, season=2026, observed_at=when or datetime.now(UTC))


def test_nflverse_first_and_espn_first_share_one_identity(capture, db):
    game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
    with db() as s:
        nflverse(s, game)
        s.commit()
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    assert game["canonical_game_id"] == "2026_00_CAR_ARI"
    with db() as s:
        assert {r.canonical_game_id for r in s.scalars(select(ScheduleObservation))} == {game["canonical_game_id"]}
        assert {q.canonical_game_id for q in s.scalars(select(OddsQuote))} == {game["canonical_game_id"]}
        before = len(list(s.scalars(select(ScheduleObservation))))
        nflverse(s, game)
        s.commit()
        assert len(list(s.scalars(select(ScheduleObservation)))) == before
    assert capture.persist([game], data_mode_value="LIVE_RESEARCH")["quotes_written"] == 0


def test_espn_first_nflverse_adopts_existing_identity_without_rewriting_history(capture, db):
    game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    original = game["canonical_game_id"]
    with db() as s:
        quote_ids = [q.id for q in s.scalars(select(OddsQuote))]
        nflverse(s, game)
        s.commit()
        assert {r.canonical_game_id for r in s.scalars(select(ScheduleObservation))} == {original}
        assert [q.id for q in s.scalars(select(OddsQuote))] == quote_ids
        assert {r.provider_game_id for r in s.scalars(select(ScheduleObservation))} == {"1", "2026_00_CAR_ARI"}


def test_next_date_revision_retains_odds_forecast_result_and_original_rows(capture, db):
    game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    original = game["canonical_game_id"]
    with db() as s:
        first = s.scalars(select(ScheduleObservation)).one()
        original_kickoff = first.kickoff_utc
        s.add(WeatherForecastVintage(data_mode="LIVE_RESEARCH", canonical_game_id=original,
              provider="nws", observed_at=datetime.now(UTC), raw_hash="preserved-weather"))
        s.commit()
    game["kickoff_utc"] += timedelta(days=1)
    game["canonical_game_id"] = "DATE_CHANGED_PROPOSAL"
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    assert game["canonical_game_id"] == original
    with db() as s:
        rows = list(s.scalars(select(ScheduleObservation).order_by(ScheduleObservation.id)))
        assert len(rows) == 2 and rows[1].supersedes_id == rows[0].id
        assert rows[0].kickoff_utc == original_kickoff
        assert s.scalars(select(WeatherForecastVintage)).one().canonical_game_id == original
        assert {q.canonical_game_id for q in s.scalars(select(OddsQuote))} == {original}
        nflverse(s, game)
        s.commit()
    game.update(status="STATUS_FINAL", completed=True)
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    with db() as s:
        assert result_scores(s, canonical_game_id=original, data_mode=DataMode.LIVE_RESEARCH) == (30, 33)
        assert {r.canonical_game_id for r in s.scalars(select(ScheduleObservation))} == {original}


def test_disagreeing_unbound_provider_is_flagged_without_prices_or_final(capture, db):
    game = _game(status="STATUS_FINAL", completed=True, quotes=PRICES)
    with db() as s:
        nflverse(s, game)
        s.commit()
    game["kickoff_utc"] += timedelta(days=1)
    counts = capture.persist([game], data_mode_value="LIVE_RESEARCH")
    assert counts["identity_refused"] == 1 and counts["quotes_written"] == counts["results_recorded"] == 0
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    with db() as s:
        assert len(list(s.scalars(select(GameIdentityReview)))) == 1
        assert len(list(s.scalars(select(ScheduleObservation)))) == 1


def test_legacy_duplicate_identities_are_not_merged(capture, db):
    game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
    with db() as s:
        for gid in ("LEGACY_A", "LEGACY_B"):
            s.add(ScheduleObservation(data_mode="LIVE_RESEARCH", canonical_game_id=gid,
                provider="nflverse", provider_game_id=gid, season=2026, season_type="PRE", week=0,
                home_team_id="ARI", away_team_id="CAR", kickoff_utc=game["kickoff_utc"],
                game_status="SCHEDULED", observed_at=datetime.now(UTC), content_hash=gid))
        s.commit()
    assert capture.persist([game], data_mode_value="LIVE_RESEARCH")["identity_refused"] == 1
    with db() as s:
        assert {r.canonical_game_id for r in s.scalars(select(ScheduleObservation))} == {"LEGACY_A", "LEGACY_B"}
        assert s.scalars(select(GameIdentityReview)).one().evidence["candidates"] == ["LEGACY_A", "LEGACY_B"]


def test_bound_provider_participant_change_and_reversed_roles_are_refused(capture, db):
    game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    original = game["canonical_game_id"]
    game["home"] = "CHI"
    assert capture.persist([game], data_mode_value="LIVE_RESEARCH")["identity_refused"] == 1
    game.update(home="CAR", away="ARI", provider_event_id="different-event")
    assert capture.persist([game], data_mode_value="LIVE_RESEARCH")["identity_refused"] == 1
    with db() as s:
        assert {r.canonical_game_id for r in s.scalars(select(ScheduleObservation))} == {original}
        reasons = {r.reason for r in s.scalars(select(GameIdentityReview))}
        assert reasons == {"PARTICIPANT_OR_SEASON_DISAGREEMENT", "HOME_AWAY_DISAGREEMENT"}


def test_nflverse_bound_source_revision_does_not_move_research_assessment(capture, db):
    from fde_api.db.forward_models import ForwardLedgerEntry

    game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
    with db() as s:
        nflverse(s, game)
        s.add(ForwardLedgerEntry(data_mode="LIVE_RESEARCH", canonical_game_id="2026_00_CAR_ARI",
            cohort="burn_in", policy_version="frozen", model_version="frozen", horizon="PREGAME",
            market="SPREAD", selection="HOME", status="WATCH", reasons={"original": True},
            as_of_at=datetime.now(UTC), created_at=datetime.now(UTC)))
        s.commit()
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    game["kickoff_utc"] += timedelta(days=1)
    with db() as s:
        nflverse(s, game)
        s.commit()
        entry = s.scalars(select(ForwardLedgerEntry)).one()
        assert entry.canonical_game_id == "2026_00_CAR_ARI" and entry.reasons == {"original": True}
        assert {o.canonical_game_id for o in s.scalars(select(ScheduleObservation))} == {entry.canonical_game_id}
    capture.persist([game], data_mode_value="LIVE_RESEARCH")
    assert game["canonical_game_id"] == "2026_00_CAR_ARI"
