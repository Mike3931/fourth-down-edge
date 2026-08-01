"""Quota governance tests: ceilings, degradation, and protected captures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.models import Base
from fde_api.forward.quota import (
    QuotaConfig,
    QuotaState,
    authorize_poll,
    budget_report,
    classify,
    record_usage,
    should_poll_game,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@pytest.fixture()
def qsession() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _seed_remaining(qsession: Session, remaining: int, used: int = 1) -> None:
    record_usage(
        qsession, provider="the-odds-api",
        headers={"x-requests-remaining": str(remaining), "x-requests-used": str(used)},
        now=NOW - timedelta(hours=1),
    )


class TestClassification:
    @pytest.mark.parametrize(
        ("remaining", "expected"),
        [(None, QuotaState.OK), (50_000, QuotaState.OK), (4_000, QuotaState.CONSTRAINED),
         (1_000, QuotaState.CRITICAL), (0, QuotaState.EXHAUSTED)],
    )
    def test_states(self, remaining, expected) -> None:
        assert classify(remaining, QuotaConfig()) == expected


class TestAuthorization:
    def test_exhausted_blocks_everything_except_nothing(self, qsession: Session) -> None:
        _seed_remaining(qsession, 0)
        d = authorize_poll(qsession, hours_to_kickoff=1.0, now=NOW)
        assert d.allowed is False and d.state == QuotaState.EXHAUSTED

    def test_closing_capture_is_protected_when_critical(self, qsession: Session) -> None:
        _seed_remaining(qsession, 900)
        d = authorize_poll(qsession, hours_to_kickoff=0.2, is_closing_capture=True, now=NOW)
        assert d.allowed is True and "protected" in d.reason

    def test_critical_reserves_for_imminent_kickoffs(self, qsession: Session) -> None:
        _seed_remaining(qsession, 900)
        near = authorize_poll(qsession, hours_to_kickoff=1.0, now=NOW)
        far = authorize_poll(qsession, hours_to_kickoff=48.0, now=NOW)
        assert near.allowed is True and far.allowed is False

    def test_constrained_degrades_distant_games_first(self, qsession: Session) -> None:
        _seed_remaining(qsession, 4_000)
        far = authorize_poll(qsession, hours_to_kickoff=100.0, now=NOW)
        mid = authorize_poll(qsession, hours_to_kickoff=12.0, now=NOW)
        near = authorize_poll(qsession, hours_to_kickoff=2.0, now=NOW)
        assert far.cadence_multiplier == 4.0
        assert mid.cadence_multiplier == 2.0
        assert near.cadence_multiplier == 1.0  # near-kickoff cadence preserved
        assert all(d.allowed for d in (far, mid, near))

    def test_daily_ceiling_blocks(self, qsession: Session) -> None:
        cfg = QuotaConfig(daily_ceiling_credits=6)
        for _ in range(3):
            record_usage(qsession, provider="the-odds-api",
                         headers={"x-requests-remaining": "50000", "x-requests-used": "1"}, now=NOW)
        d = authorize_poll(qsession, hours_to_kickoff=5.0, cfg=cfg, now=NOW)
        assert d.allowed is False and "daily ceiling" in d.reason

    def test_unknown_remaining_is_permissive_but_capped(self, qsession: Session) -> None:
        d = authorize_poll(qsession, hours_to_kickoff=5.0, now=NOW)
        assert d.allowed is True and d.remaining is None


class TestGameEligibility:
    def test_completed_game_not_polled(self) -> None:
        ok, why = should_poll_game(kickoff_utc=NOW - timedelta(hours=4),
                                   game_status="SCHEDULED", has_final_score=True, now=NOW)
        assert ok is False and "final score" in why

    def test_postponed_and_cancelled_not_polled(self) -> None:
        for st in ("POSTPONED", "CANCELLED"):
            ok, _ = should_poll_game(kickoff_utc=NOW + timedelta(days=1),
                                     game_status=st, has_final_score=False, now=NOW)
            assert ok is False

    def test_after_kickoff_not_polled(self) -> None:
        ok, why = should_poll_game(kickoff_utc=NOW - timedelta(minutes=30),
                                   game_status="SCHEDULED", has_final_score=False, now=NOW)
        assert ok is False and "in-play" in why

    def test_upcoming_game_polled(self) -> None:
        ok, _ = should_poll_game(kickoff_utc=NOW + timedelta(hours=3),
                                 game_status="SCHEDULED", has_final_score=False, now=NOW)
        assert ok is True

    def test_unresolved_kickoff_not_polled(self) -> None:
        ok, _ = should_poll_game(kickoff_utc=None, game_status="SCHEDULED",
                                 has_final_score=False, now=NOW)
        assert ok is False


class TestUsageRecording:
    def test_headers_persisted(self, qsession: Session) -> None:
        row = record_usage(qsession, provider="the-odds-api",
                           headers={"x-requests-remaining": "18500", "x-requests-used": "1500"}, now=NOW)
        assert row.calls_remaining == 18_500 and row.calls_used == 1_500

    def test_missing_headers_do_not_crash(self, qsession: Session) -> None:
        row = record_usage(qsession, provider="the-odds-api", headers={}, now=NOW)
        assert row.calls_remaining is None and row.calls_used == 1

    def test_malformed_headers_do_not_crash(self, qsession: Session) -> None:
        row = record_usage(qsession, provider="the-odds-api",
                           headers={"x-requests-remaining": "not-a-number"}, now=NOW)
        assert row.calls_remaining is None


class TestBudget:
    def test_budget_is_documented_and_consistent(self) -> None:
        b = budget_report()
        assert b["credits_per_request"] == 3
        assert b["monthly_requirement_credits"] > b["monthly_base_credits"]
        assert b["minimum_viable_plan_credits"] == 20_000
        assert "missing close" in b["compromise"]
        assert b["tiers"], "forecast must be generated from cadence tiers"
