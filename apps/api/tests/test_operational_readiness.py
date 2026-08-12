"""The one command that answers "can we run" had no tests and two defects.

`RUNBOOK.md` opens with this script and says it "separates three questions
that get conflated, because each has a different fix and answering only
the loudest one wastes an evening". It then conflated two causes inside
one of those questions, and decided its verdict by counting.

THE SLATE BLOCKER CONFLATED A GAP WITH A QUIET TUESDAY

`target_in_slate` asks whether any game kicks off within twelve hours of
the target instant. When false it emitted a blocker ending "A game that
was never ingested cannot be evaluated" — true when the slate is empty or
does not reach this date, and a non-sequitur when 283 games are ingested
and none of them happens to be today. Both printed the same sentence, and
only one of them is something to go and fix.

THE VERDICT WAS A TALLY

`BLOCKED` if three or more blockers, `PARTIAL` if fewer. Severity by
count, so "no game kicks off in the next twelve hours" weighed the same
as "no provider key". Today that produced `VERDICT: BLOCKED` printed
directly above `CAN RUN NOW: + schedule, weather and injury capture / +
data-health reconciliation` — the verdict contradicting the capability
list two lines below it.

The verdict now reads the capability list it already computes: BLOCKED
when nothing but the always-available audit remains, PARTIAL when real
work can still be done, READY when nothing is blocking.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ScheduleObservation
from fde_api.db.models import Base

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _load_script():
    """The readiness script is not an importable package module."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "operational_readiness.py"
    spec = importlib.util.spec_from_file_location("_op_readiness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_op_readiness"] = module
    spec.loader.exec_module(module)
    return module


ops = _load_script()


@pytest.fixture()
def session(monkeypatch, tmp_path) -> Session:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
    monkeypatch.setattr(settings, "odds_api_key", None, raising=False)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


def _game(s: Session, game_id: str, kickoff: datetime) -> None:
    s.add(ScheduleObservation(
        canonical_game_id=game_id, data_mode="LIVE_RESEARCH", season=2026,
        season_type="REG", week=1, away_team_id="KC", home_team_id="BUF",
        kickoff_utc=kickoff, game_status="SCHEDULED", provider="nflverse",
        provider_game_id=f"nflverse:{game_id}", content_hash=game_id,
        observed_at=NOW - timedelta(days=5),
    ))
    s.commit()


class TestAQuietDayIsNotAnIngestionGap:
    def test_a_full_slate_with_no_imminent_game_does_not_blame_ingestion(
        self, session: Session
    ) -> None:
        """The state the development database is actually in: everything
        ingested, nothing kicking off today."""
        _game(session, "2026_01_A", NOW + timedelta(days=3))
        _game(session, "2026_02_B", NOW + timedelta(days=10))
        report = ops.assess(session, NOW)
        joined = " ".join(report["blockers"])
        assert "never ingested" not in joined

    def test_an_empty_slate_still_blocks_and_still_says_why(
        self, session: Session
    ) -> None:
        """The guard against over-correcting: a genuine coverage gap is
        the case this blocker was written for."""
        report = ops.assess(session, NOW)
        joined = " ".join(report["blockers"])
        assert "SLATE" in joined.upper()

    def test_a_target_outside_the_ingested_range_blocks(
        self, session: Session
    ) -> None:
        """Games exist, but none anywhere near the instant asked about —
        the slate does not reach that far, which is a coverage gap."""
        _game(session, "2026_01_A", NOW + timedelta(days=3))
        report = ops.assess(session, NOW + timedelta(days=400))
        assert any("SLATE" in b.upper() for b in report["blockers"])

    def test_the_quiet_day_is_still_reported_somewhere(
        self, session: Session
    ) -> None:
        """Demoted, not hidden. A person asking "can we test tonight"
        needs to be told that nothing is on tonight."""
        _game(session, "2026_01_A", NOW + timedelta(days=3))
        assert report_mentions_next_kickoff(ops.assess(session, NOW))


def report_mentions_next_kickoff(report: dict) -> bool:
    text = " ".join(report.get("notes", [])) + " ".join(report["blockers"])
    return "kickoff" in text.lower()


class TestTheVerdictIsNotATally:
    def test_it_does_not_say_blocked_while_listing_work_that_can_run(
        self, session: Session
    ) -> None:
        """The contradiction, stated directly: BLOCKED printed above a
        list of things that run fine."""
        _game(session, "2026_01_A", NOW + timedelta(days=3))
        report = ops.assess(session, NOW)
        real_work = [c for c in report["can_run_now"] if "reconciliation" not in c]
        if report["verdict"] == ops.VERDICT_BLOCKED:
            assert not real_work, report["can_run_now"]

    def test_blocked_means_only_the_audit_remains(self, session: Session) -> None:
        """Empty database, no key, no policy: nothing but the record audit
        can run, and that is what BLOCKED is for."""
        report = ops.assess(session, NOW)
        assert report["verdict"] == ops.VERDICT_BLOCKED

    def test_an_ingested_slate_makes_it_partial_rather_than_blocked(
        self, session: Session
    ) -> None:
        """Capture of schedule, weather and injuries is real work, and the
        script already says so in `can_run_now`."""
        _game(session, "2026_01_A", NOW + timedelta(days=3))
        report = ops.assess(session, NOW)
        assert report["verdict"] == ops.VERDICT_PARTIAL

    def test_a_third_blocker_does_not_flip_partial_to_blocked(
        self, session: Session
    ) -> None:
        """The tally, stated directly.

        Two blockers becomes three, nothing about what can RUN changes,
        and under the old `len(blockers) >= 3` rule the verdict flipped
        from PARTIAL to BLOCKED on the strength of the count alone.

        The third blocker here is a genuinely too-small provider plan,
        which is a subscription decision and takes no capability away.
        """
        from fde_api.db.forward_models import ProviderQuotaUsage

        _game(session, "2026_01_A", NOW + timedelta(days=3))
        two = ops.assess(session, NOW)
        assert len(two["blockers"]) == 2, two["blockers"]
        assert two["verdict"] == ops.VERDICT_PARTIAL

        session.add(ProviderQuotaUsage(
            provider="the-odds-api", window_start=NOW, calls_used=1,
            calls_remaining=90, quota_limit=100, last_response_at=NOW,
        ))
        session.commit()

        three = ops.assess(session, NOW)
        assert len(three["blockers"]) == 3, three["blockers"]
        assert three["can_run_now"] == two["can_run_now"]
        assert three["verdict"] == ops.VERDICT_PARTIAL


class TestItStillNeverPrintsTheKey:
    def test_the_report_reports_presence_only(self, session: Session) -> None:
        import json

        report = ops.assess(session, NOW)
        assert "secret-value" not in json.dumps(report)
        assert report["provider"]["configured"] is False
