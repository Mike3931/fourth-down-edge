from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import OddsQuote, ScheduleObservation
from fde_api.db.models import Base, Job
from fde_api.forward.briefing import KIND, prepare_briefing, read_briefing

NOW = datetime(2026, 10, 13, 1, tzinfo=UTC)  # Monday evening CT, Tuesday UTC


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def schedule(s, gid="REAL", *, observed=None, kickoff=None, mode="LIVE_RESEARCH"):
    s.add(ScheduleObservation(canonical_game_id=gid, data_mode=mode, season=2026,
          season_type="REG", week=6, away_team_id="BUF", home_team_id="KC",
          kickoff_utc=kickoff or NOW + timedelta(hours=1), provider="espn",
          provider_game_id=gid, content_hash=gid, game_status="SCHEDULED",
          observed_at=observed or NOW - timedelta(minutes=1)))
    s.flush()


def quote(s, *, mode="LIVE", observed=None, book="draftkings", raw_hash="q"):
    s.add(OddsQuote(canonical_game_id="REAL", data_mode="LIVE_RESEARCH", provider="espn",
          provider_mode=mode, sportsbook=book, market="MONEYLINE", selection="HOME",
          american=-110, decimal_odds=1.909, observed_at=observed or NOW, raw_hash=raw_hash))
    s.flush()


def test_local_slate_has_no_historical_cap_or_demo_leak(session):
    for i in range(405):
        schedule(session, f"H{i}", kickoff=NOW - timedelta(days=10))
    schedule(session)
    schedule(session, "DEMO", mode="DEMO")
    schedule(session, "FUTURE", observed=NOW + timedelta(hours=1))
    quote(session)
    data = prepare_briefing(session, now=NOW)
    assert [g["canonical_game_id"] for g in data["games"]] == ["REAL"]
    assert data["today_count"] == 1 and data["observed_market_games"] == 1
    assert data["qualified_count"] == data["verified_bet365_count"] == 0
    assert data["decision"] == "NO_BET"


@pytest.mark.parametrize("book", ["draftkings", "bet365", "bet365_au"])
def test_book_label_never_proves_tennessee_availability(session, book):
    schedule(session)
    quote(session, book=book)
    assert prepare_briefing(session, now=NOW)["verified_bet365_count"] == 0


def test_fixture_and_future_quotes_cannot_be_counted(session):
    schedule(session)
    quote(session, mode="FIXTURE")
    quote(session, observed=NOW + timedelta(seconds=1), raw_hash="future")
    assert prepare_briefing(session, now=NOW)["observed_market_games"] == 0


def test_read_is_not_a_research_run_and_never_writes(session):
    schedule(session)
    assert read_briefing(session, now=NOW)["latest_run"] is None
    assert list(session.scalars(select(Job))) == []


def test_recent_prepared_manifest_reused_and_new_identity_evidence_invalidates(session, monkeypatch):
    from fde_api.db.research_models import PlayerIdentityReview
    from fde_api.forward import briefing
    schedule(session)
    stored = prepare_briefing(session, now=NOW)
    session.add(Job(id="prepared", kind=KIND, params={}, status="finished", created_at=NOW,
                    finished_at=NOW, result={"briefing": stored}))
    session.flush()
    original = briefing.prepare_briefing
    calls = []
    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(briefing, "prepare_briefing", tracked)
    result = read_briefing(session, now=NOW + timedelta(minutes=1))
    assert calls == [] and result["as_of_at"] == stored["as_of_at"]
    assert "latest_run" not in stored  # Read decorations never mutate the frozen payload.
    session.add(PlayerIdentityReview(id="new", player_id="PLAYER", reason="PROVIDER_ID_COLLISION",
        evidence={}, observed_at=NOW + timedelta(seconds=30)))
    session.flush()
    read_briefing(session, now=NOW + timedelta(minutes=1))
    assert calls == [True]


def test_failed_attempt_is_not_hidden_by_old_success(session):
    schedule(session)
    session.add(Job(id="old", kind=KIND, params={}, status="finished",
                    created_at=NOW - timedelta(hours=2), result={"briefing": prepare_briefing(session, now=NOW)}))
    session.add(Job(id="failure", kind=KIND, params={}, status="failed", error="HTTPError",
                    created_at=NOW - timedelta(minutes=1)))
    session.flush()
    assert read_briefing(session, now=NOW)["latest_run"]["status"] == "failed"


def test_changed_price_is_evidence_change_not_fake_confidence(session):
    schedule(session)
    quote(session)
    before = prepare_briefing(session, now=NOW)
    session.add(Job(id="before", kind=KIND, params={}, status="finished", created_at=NOW - timedelta(minutes=2), result={"briefing": before}))
    quote(session, raw_hash="changed-price", observed=NOW + timedelta(seconds=1))
    after = prepare_briefing(session, now=NOW + timedelta(seconds=2))
    session.add(Job(id="after", kind=KIND, params={}, status="finished", created_at=NOW, result={"briefing": after}))
    session.flush()
    view = read_briefing(session, now=NOW + timedelta(seconds=2))
    assert view["changes"][0]["fields"] == ["quote_evidence"]
    assert view["qualified_count"] == 0


def test_requires_aware_point_in_time_cutoff(session):
    with pytest.raises(ValueError):
        prepare_briefing(session, now=NOW.replace(tzinfo=None))


def test_runner_records_failures_without_leaking_credentials(session):
    from sqlalchemy.orm import sessionmaker

    from fde_api.forward.research_runner import run_cycle

    def fail():
        raise RuntimeError("https://provider.test?apiKey=private-secret")

    factory = sessionmaker(bind=session.get_bind())
    result = run_cycle(factory, fail, dates="20261012")
    row = session.get(Job, result["run_id"])
    assert row.status == "failed" and row.result is None
    assert "private-secret" not in row.error
    assert row.finished_at is not None


def test_runner_preserves_completed_manifests_and_reports_unchanged_capture(session):
    from sqlalchemy.orm import sessionmaker

    from fde_api.forward.research_runner import run_cycle

    factory = sessionmaker(bind=session.get_bind())
    first = run_cycle(factory, lambda: {"quotes_written": 0}, dates="20261012")
    before = session.get(Job, first["run_id"]).result
    second = run_cycle(factory, lambda: {"quotes_duplicate": 0}, dates="20261012")
    assert first["run_id"] != second["run_id"]
    assert session.get(Job, first["run_id"]).result == before
    assert session.get(Job, second["run_id"]).status == "finished"


def test_worker_refuses_original_database_and_overlapping_run(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_research.py"
    original = tmp_path / "fde.db"
    original.write_bytes(b"protected original")
    isolated = tmp_path / "research.db"
    isolated.write_bytes(b"isolated")
    isolated.with_name("research.db.research.lock").touch()
    for path in [original, isolated]:
        result = subprocess.run([sys.executable, str(script), "--database-file", str(path)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 2
    assert original.read_bytes() == b"protected original"


def test_three_equal_time_assessments_are_all_preserved(session):
    from fde_api.db.forward_models import ForwardLedgerEntry

    schedule(session)
    for status in ["WATCH", "PASS", "DATA_INCOMPLETE"]:
        session.add(ForwardLedgerEntry(data_mode="LIVE_RESEARCH", cohort="burn_in",
            canonical_game_id="REAL", policy_version="frozen", model_version="research",
            horizon="PREGAME", market="MONEYLINE", selection="HOME", status=status,
            reasons={}, as_of_at=NOW, created_at=NOW))
    session.flush()
    game = prepare_briefing(session, now=NOW)["games"][0]
    assert len(game["research_records"]) == 3
    assert "Equal-time research assessments require review; none was selected" in game["missing"]
    assert len(list(session.scalars(select(ForwardLedgerEntry)))) == 3


@pytest.mark.parametrize("age", [None, -1, 49])
def test_unknown_future_or_stale_availability_does_not_clear_input_gate(session, age):
    from fde_api.db.forward_models import InjuryObservation

    schedule(session)
    session.add(InjuryObservation(data_mode="LIVE_RESEARCH", canonical_game_id="REAL",
        team_id="KC", player_id="GSIS-TEST", report_date="2026-10-12", game_designation="QUESTIONABLE",
        source_category="OFFICIAL_VERIFIED", verification_status="VERIFIED", confidence="HIGH",
        source_updated_at=None if age is None else NOW - timedelta(hours=age),
        observed_at=NOW, entered_at=NOW))
    session.flush()
    game = prepare_briefing(session, now=NOW)["games"][0]
    assert game["inputs"]["availability"] == "UNAVAILABLE"
    assert "Fresh verified player availability evidence" in game["missing"]
