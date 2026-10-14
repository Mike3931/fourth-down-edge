import csv
import io
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from fde_api.db.forward_models import GameIdentityBinding, ScheduleObservation
from fde_api.db.models import Player
from fde_api.db.research_models import (
    PlayerBinding,
    PlayerIdentityReview,
    ResearchFact,
    ResearchPoll,
    ResearchSource,
)
from fde_api.forward.intelligence import IntelligenceAt, collect_intelligence, ingest_source
from fde_api.pit.guards import LookaheadError

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def csv_bytes(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def ingest(s, dataset, rows, now=NOW):
    return ingest_source(s, dataset=dataset, season=2026, payload=csv_bytes(rows),
                         captured_at=now, published_at=now - timedelta(minutes=1))


def player(gsis="00-0000001", espn="101", name="Same Name"):
    return {"gsis_id": gsis, "espn_id": espn, "birth_date": "1995-01-01",
            "display_name": name, "position": "QB"}


def roster(team="KC", week="6", position="QB"):
    return {"season": "2026", "team": team, "gsis_id": "00-0000001", "espn_id": "101",
            "position": position, "week": week, "status": "ACT", "full_name": "Same Name"}


def fixture(s):
    for gid, week, status, kickoff in (("PAST", 5, "FINAL", NOW - timedelta(days=2)),
                                      ("NEXT", 6, "SCHEDULED", NOW + timedelta(days=2))):
        s.add(ScheduleObservation(canonical_game_id=gid, data_mode="LIVE_RESEARCH", season=2026,
            season_type="REG", week=week, home_team_id="KC", away_team_id="BUF", kickoff_utc=kickoff,
            provider="nflverse", provider_game_id=gid, game_status=status, content_hash=gid,
            observed_at=NOW - timedelta(days=3)))
    s.add(GameIdentityBinding(data_mode="LIVE_RESEARCH", provider="nflverse", provider_game_id="PAST",
        canonical_game_id="PAST", evidence={}, created_at=NOW - timedelta(days=3)))
    s.flush()
    return s.scalars(select(ScheduleObservation).where(ScheduleObservation.canonical_game_id == "NEXT")).one()


def stats(player_row=False, yards="250"):
    row = {"season": "2026", "week": "5", "team": "KC", "game_id": "PAST",
           "season_type": "REG", "passing_epa": "5.0", "attempts": "30", "passing_yards": yards}
    if player_row:
        row.update(player_id="00-0000001", targets="0", position="QB")
    return row


def test_same_names_do_not_join_and_provider_collisions_block_both(session):
    batch = ingest(session, "players", [player(), player("00-0000002", "102")])
    assert batch.coverage["mapped_players"] == 2
    assert len(list(session.scalars(select(Player)))) == 2
    ingest(session, "players", [player(), player("00-0000002", "101")], NOW + timedelta(hours=1))
    assert len(list(session.scalars(select(PlayerBinding).where(PlayerBinding.provider == "espn")))) == 2
    assert len(list(session.scalars(select(PlayerIdentityReview)))) == 2


def test_revisions_repeat_ingestion_and_known_at_leakage(session):
    game = fixture(session)
    ingest(session, "players", [player()])
    old = ingest(session, "rosters", [roster()])
    first = ingest(session, "player_stats", [stats(True)])
    ingest(session, "team_stats", [stats()])
    view = IntelligenceAt(session, NOW).game(game)
    kc = view["teams"][1]
    assert kc["players"][0]["metrics"]["passing_yards"] == 250
    assert kc["recent_games"][0]["canonical_game_id"] == "PAST"
    assert not kc["availability_complete"]
    assert IntelligenceAt(session, NOW - timedelta(seconds=1)).game(game)["teams"][1]["players"] == []
    revised = ingest(session, "player_stats", [stats(True, "999")], NOW + timedelta(hours=1))
    assert revised.id != first.id
    assert IntelligenceAt(session, NOW).game(game)["teams"][1]["players"][0]["metrics"]["passing_yards"] == 250
    assert ingest(session, "rosters", [roster()], NOW + timedelta(hours=2)).id == old.id
    assert session.get(ResearchSource, old.id).captured_at == NOW
    assert len(list(session.scalars(select(ResearchFact).where(ResearchFact.source_id == old.id)))) == 1


def test_trade_preserves_previous_assignment_and_does_not_mix_old_team_stats(session):
    game = fixture(session)
    ingest(session, "players", [player()])
    old = ingest(session, "rosters", [roster()])
    ingest(session, "player_stats", [stats(True)])
    new = ingest(session, "rosters", [roster("BUF")], NOW + timedelta(hours=1))
    assert old.id != new.id
    assert IntelligenceAt(session, NOW).game(game)["teams"][1]["roster_count"] == 1
    later = IntelligenceAt(session, NOW + timedelta(hours=1)).game(game)
    assert later["teams"][0]["roster_count"] == 1
    assert later["teams"][0]["players"] == []
    assert later["teams"][1]["roster_count"] == 0


def test_future_publication_unknown_identity_and_position_disagreement(session):
    with pytest.raises(LookaheadError):
        ingest_source(session, dataset="players", season=2026, payload=csv_bytes([player()]),
                      captured_at=NOW, published_at=NOW + timedelta(seconds=1))
    game = fixture(session)
    batch = ingest(session, "rosters", [roster()])
    assert batch.coverage["refused_rows"] == 1
    ingest(session, "players", [player()])
    ingest(session, "rosters", [roster(position="WR")], NOW + timedelta(minutes=1))
    ingest(session, "player_stats", [stats(True)], NOW + timedelta(minutes=1))
    view = IntelligenceAt(session, NOW + timedelta(minutes=1)).game(game)
    assert view["teams"][1]["players"] == []
    assert any("position disagreement" in reason for reason in view["missing"])


def test_conflicting_rows_refused_and_secondary_injury_is_not_confirmation(session):
    game = fixture(session)
    ingest(session, "players", [player()])
    batch = ingest(session, "rosters", [roster(), roster(position="WR")])
    assert batch.coverage["accepted_rows"] == 0 and batch.coverage["refused_rows"] == 2
    ingest(session, "rosters", [roster()], NOW + timedelta(seconds=1))
    ingest(session, "injuries", [{"season": "2026", "week": "6", "team": "KC",
        "gsis_id": "00-0000001", "report_status": "Out", "practice_status": "Did Not Participate"}],
        NOW + timedelta(seconds=1))
    evidence = IntelligenceAt(session, NOW + timedelta(seconds=1)).game(game)["teams"][1]
    assert evidence["availability"][0]["status"] == "SECONDARY_UNVERIFIED"
    assert evidence["verified_availability"] == [] and not evidence["availability_complete"]


def test_persisted_poll_intervals_failure_and_recovery(session, tmp_path, monkeypatch):
    from fde_api.forward import intelligence
    monkeypatch.setattr(intelligence, "INTERVALS", {"players": 60})
    monkeypatch.setattr(intelligence.settings, "raw_dir", tmp_path)
    fail = False
    def response(request):
        return httpx.Response(503 if fail else 200, content=csv_bytes([player()]),
                              headers={"Last-Modified": "Tue, 13 Oct 2026 11:59:00 GMT"})
    client = httpx.Client(transport=httpx.MockTransport(response))
    assert collect_intelligence(session, now=NOW, season=2026, client=client, clock=lambda: NOW)["research_downloads"] == 1
    assert collect_intelligence(session, now=NOW, season=2026, client=client, clock=lambda: NOW)["research_not_due"] == 1
    fail = True
    assert collect_intelligence(session, now=NOW + timedelta(seconds=61), season=2026, client=client, clock=lambda: NOW)["research_failures"] == 1
    assert IntelligenceAt(session, NOW + timedelta(seconds=61)).polls[(2026, "players")].status == "FAILED"
    fail = False
    assert collect_intelligence(session, now=NOW + timedelta(seconds=122), season=2026, client=client, clock=lambda: NOW)["research_downloads"] == 1
    assert len(list(session.scalars(select(ResearchSource)))) == 1
    assert len(list(session.scalars(select(ResearchPoll)))) == 3


def test_snap_counts_use_pfr_identity_and_same_completed_game(session):
    game = fixture(session)
    ingest(session, "players", [{**player(), "pfr_id": "Player01"}])
    ingest(session, "rosters", [roster()])
    ingest(session, "player_stats", [stats(True)])
    snap = {"season": "2026", "week": "5", "team": "KC", "game_id": "PAST",
            "pfr_player_id": "Player01", "offense_pct": "0.8", "offense_snaps": "40"}
    ingest(session, "snap_counts", [snap])
    assert IntelligenceAt(session, NOW).game(game)["teams"][1]["players"][0]["snap_share"] == 0.8
    bad = ingest(session, "snap_counts", [{**snap, "offense_pct": "1.5"}], NOW + timedelta(seconds=1))
    assert bad.coverage["accepted_rows"] == 0 and bad.coverage["refused_rows"] == 1


def test_identical_payload_recovers_after_identity_is_known_without_backdating(session):
    game = fixture(session)
    refused = ingest(session, "rosters", [roster()])
    assert refused.coverage["accepted_rows"] == 0
    ingest(session, "players", [player()], NOW + timedelta(minutes=1))
    recovered = ingest(session, "rosters", [roster()], NOW + timedelta(minutes=2))
    assert recovered.id != refused.id and recovered.payload_hash == refused.payload_hash
    assert recovered.coverage["accepted_rows"] == 1
    assert IntelligenceAt(session, NOW).game(game)["teams"][1]["roster_count"] == 0
    assert IntelligenceAt(session, NOW + timedelta(minutes=2)).game(game)["teams"][1]["roster_count"] == 1


def test_full_schedule_results_are_governed_known_at_capture_and_conflicts_refused(session):
    from fde_api.forward.modes import DataMode
    from fde_api.forward.results import _score_of, final_observation
    row = {"game_id": "2026_05_BUF_KC", "season": "2026", "week": "5", "game_type": "REG",
           "home_team": "KC", "away_team": "BUF", "gameday": "2026-10-11", "gametime": "08:00",
           "home_score": "24", "away_score": "20"}
    batch = ingest(session, "schedule", [row])
    assert batch.coverage["results_recorded"] == 1
    assert final_observation(session, canonical_game_id=row["game_id"], data_mode=DataMode.LIVE_RESEARCH,
                             as_of=NOW - timedelta(seconds=1)) is None
    original = final_observation(session, canonical_game_id=row["game_id"], data_mode=DataMode.LIVE_RESEARCH)
    assert _score_of(original) == (24, 20)
    conflict = ingest(session, "schedule", [{**row, "home_score": "99"}], NOW + timedelta(minutes=1))
    assert conflict.coverage["results_refused"] == 1
    assert _score_of(final_observation(session, canonical_game_id=row["game_id"],
                    data_mode=DataMode.LIVE_RESEARCH)) == (24, 20)


def test_same_week_ambiguous_team_assignment_is_not_a_trade_resolution(session):
    game = fixture(session)
    ingest(session, "players", [player()])
    source = ingest(session, "rosters", [roster("KC"), roster("BUF")])
    assert source.coverage["accepted_rows"] == 0 and source.coverage["refused_rows"] == 2
    assert all(t["roster_count"] == 0 for t in IntelligenceAt(session, NOW).game(game)["teams"])
    assert all("Ambiguous" in r.evidence["refusal_reason"] for r in session.scalars(select(PlayerIdentityReview)))


def test_network_response_is_known_when_received_not_when_poll_started(session, tmp_path, monkeypatch):
    from fde_api.forward import intelligence
    monkeypatch.setattr(intelligence, "INTERVALS", {"players": 60})
    monkeypatch.setattr(intelligence.settings, "raw_dir", tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
        content=csv_bytes([player()]), headers={"Last-Modified": "Tue, 13 Oct 2026 11:59:00 GMT"})))
    received = NOW + timedelta(seconds=5)
    collect_intelligence(session, now=NOW, season=2026, client=client, clock=lambda: received)
    source = session.scalars(select(ResearchSource)).one()
    assert source.captured_at == received
    assert IntelligenceAt(session, NOW + timedelta(seconds=2)).sources == {}


def test_statistics_cannot_claim_another_games_team_or_week(session):
    fixture(session)
    ingest(session, "players", [player()])
    wrong_team = ingest(session, "player_stats", [{**stats(True), "team": "TEN"}])
    assert wrong_team.coverage["accepted_rows"] == 0
    assert wrong_team.coverage["refused_rows"] == 1
    wrong_week = ingest(session, "player_stats", [{**stats(True), "week": "4"}], NOW + timedelta(seconds=1))
    assert wrong_week.coverage["accepted_rows"] == 0
    assert wrong_week.coverage["refused_rows"] == 1


def test_normalization_upgrade_refreshes_without_backdating_old_evidence(session, tmp_path, monkeypatch):
    from fde_api.forward import intelligence
    monkeypatch.setattr(intelligence, "INTERVALS", {"players": 86400})
    monkeypatch.setattr(intelligence.settings, "raw_dir", tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,
        content=csv_bytes([player()]), headers={"Last-Modified": "Tue, 13 Oct 2026 11:59:00 GMT"})))
    collect_intelligence(session, now=NOW, season=2026, client=client, clock=lambda: NOW)
    monkeypatch.setattr(intelligence, "VERSION", "test-new-normalization")
    result = collect_intelligence(session, now=NOW + timedelta(seconds=1), season=2026,
                                  client=client, clock=lambda: NOW + timedelta(seconds=1))
    assert result["research_downloads"] == 1
    assert len(list(session.scalars(select(ResearchSource)))) == 2
    assert len(list(session.scalars(select(PlayerBinding)))) == 2  # GSIS plus ESPN; never duplicated.
