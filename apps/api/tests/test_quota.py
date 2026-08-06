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


PLAN = QuotaConfig().monthly_plan_credits


def _seed_remaining(
    qsession: Session, remaining: int, used: int | None = None, plan: int = PLAN
) -> None:
    """Seed a balance ON A STATED PLAN.

    `used` used to default to 1, which made the implied plan
    `remaining + 1` - so "900 credits left" described a 901-credit account
    rather than a nearly-spent 20,000 one. That was invisible while the
    thresholds were absolute and became visible the moment they scaled,
    which is the behaviour under test working as intended.
    """
    if used is None:
        used = max(0, plan - remaining)
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
        """`calls_used` is THIS call's requests, not the month-to-date total.

        It used to store the provider's cumulative `x-requests-used`, which
        `credits_used_since` then SUMMED across rows - so after n polls the
        daily total read 1+2+...+n instead of n, and the ceiling slammed
        shut blaming a budget that had never been spent. The cumulative
        figure is not lost; it is `quota_limit - calls_remaining`.
        """
        row = record_usage(qsession, provider="the-odds-api",
                           headers={"x-requests-remaining": "18500", "x-requests-used": "1500"}, now=NOW)
        assert row.calls_remaining == 18_500
        assert row.calls_used == 1, "cumulative counter stored as this call's cost"
        assert row.quota_limit == 20_000, "the plan, learned from both halves"

    def test_the_month_to_date_total_is_still_recoverable(self, qsession: Session) -> None:
        from fde_api.forward.quota import month_to_date_used

        record_usage(qsession, provider="the-odds-api",
                     headers={"x-requests-remaining": "18500", "x-requests-used": "1500"},
                     now=NOW)
        assert month_to_date_used(qsession) == 1_500

    def test_repeated_polls_do_not_inflate_the_daily_total(self, qsession: Session) -> None:
        """The bug, at the shape it actually bit: five polls must count as
        five, not fifteen."""
        from fde_api.forward.quota import credits_used_since

        for i in range(5):
            record_usage(
                qsession, provider="the-odds-api",
                headers={"x-requests-remaining": str(19_995 - i),
                         "x-requests-used": str(5 + i)},
                now=NOW + timedelta(minutes=i),
            )
        assert credits_used_since(qsession, "the-odds-api", NOW - timedelta(days=1)) == 5

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


class TestThresholdsScaleToTheRealPlan:
    """The absolutes describe a 20,000-credit plan.

    Left absolute they fail silently on a smaller one: a 500-credit account
    classifies CRITICAL at every remaining value from 1 to 1,500, so the
    scheduler drops to closing-captures-only on its first call and stays
    there. Nothing reports a misconfiguration - just a permanently unhappy
    quota state that reads like a real warning.
    """

    def test_the_configured_plan_is_unchanged(self) -> None:
        from fde_api.forward.quota import thresholds_for

        assert thresholds_for(20_000, QuotaConfig()) == (1_500, 5_000)

    def test_an_unknown_plan_keeps_the_absolutes(self) -> None:
        """Guessing small would throttle a large plan for no reason."""
        from fde_api.forward.quota import thresholds_for

        assert thresholds_for(None, QuotaConfig()) == (1_500, 5_000)
        assert thresholds_for(0, QuotaConfig()) == (1_500, 5_000)

    def test_a_small_plan_is_not_permanently_critical(self) -> None:
        cfg = QuotaConfig()
        assert classify(496, cfg) == QuotaState.CRITICAL, "the bug being fixed"
        assert classify(496, cfg, 500) == QuotaState.OK

    def test_a_small_plan_still_reaches_critical_near_the_end(self) -> None:
        """Rescaling must not remove the reserve, only right-size it."""
        cfg = QuotaConfig()
        assert classify(120, cfg, 500) == QuotaState.CONSTRAINED
        assert classify(30, cfg, 500) == QuotaState.CRITICAL
        assert classify(0, cfg, 500) == QuotaState.EXHAUSTED

    def test_the_reserve_can_always_pay_for_a_closing_capture(self) -> None:
        """A reserve too small to buy one poll is not a reserve."""
        from fde_api.forward.quota import thresholds_for

        cfg = QuotaConfig()
        for plan in (10, 50, 100, 500, 5_000, 20_000):
            reserve, _ = thresholds_for(plan, cfg)
            assert reserve >= cfg.credits_per_request, plan

    def test_the_daily_ceiling_never_exceeds_the_window(self) -> None:
        """1,000 credits a day against a 500-credit month is not a ceiling."""
        from fde_api.forward.quota import daily_ceiling_for

        cfg = QuotaConfig()
        assert daily_ceiling_for(500, cfg) == 16
        assert daily_ceiling_for(20_000, cfg) == 666
        assert daily_ceiling_for(None, cfg) == cfg.daily_ceiling_credits


class TestThePlanIsLearnedNotAssumed:
    def test_the_plan_is_inferred_from_both_headers(self, qsession: Session) -> None:
        """The provider never states the plan but states both halves of it."""
        from fde_api.forward.quota import observed_plan_credits, record_usage

        record_usage(
            qsession, provider="the-odds-api",
            headers={"x-requests-remaining": "497", "x-requests-used": "3"},
            now=NOW,
        )
        assert observed_plan_credits(qsession) == 500

    def test_a_truncated_response_cannot_shrink_the_plan(self, qsession: Session) -> None:
        """Taking the max, not the latest: one odd response mid-reset must
        not convince us the account got smaller."""
        from fde_api.forward.quota import observed_plan_credits, record_usage

        record_usage(
            qsession, provider="the-odds-api", headers={"x-requests-remaining": "497", "x-requests-used": "3"},
            now=NOW - timedelta(hours=2))
        record_usage(
            qsession, provider="the-odds-api", headers={"x-requests-remaining": "10", "x-requests-used": "0"},
            now=NOW)
        assert observed_plan_credits(qsession) == 500

    def test_an_absent_header_leaves_the_plan_unknown(self, qsession: Session) -> None:
        from fde_api.forward.quota import observed_plan_credits, record_usage

        record_usage(qsession, provider="the-odds-api", headers={}, now=NOW)
        assert observed_plan_credits(qsession) is None

    def test_capacity_is_reported_in_polls_not_credits(self) -> None:
        """"500 credits" and "about five polls a day" are the same fact,
        and only one of them is usable."""
        from fde_api.forward.quota import plan_capacity

        cap = plan_capacity(500)
        assert cap["polls_per_window"] == 166
        assert cap["polls_per_day"] == 5.5

    def test_authorisation_uses_the_observed_plan(self, qsession: Session) -> None:
        """The end-to-end shape of the bug: a real balance on a real small
        plan must not be treated as an emergency."""
        from fde_api.forward.quota import record_usage

        record_usage(
            qsession, provider="the-odds-api", headers={"x-requests-remaining": "496", "x-requests-used": "4"},
            now=NOW - timedelta(hours=1))
        d = authorize_poll(qsession, hours_to_kickoff=72.0, now=NOW)
        assert d.state == QuotaState.OK, d.reason
        assert d.allowed is True


class TestTheUnknownPlanIsNotGuessed:
    def test_a_legacy_row_without_a_limit_reports_unknown(self, qsession: Session) -> None:
        """`calls_used + calls_remaining` would read a nearly-spent 20,000
        account as a 497-credit plan. A fabricated plan that looks precise
        is worse than an honest None, which the next response corrects."""
        from fde_api.db.forward_models import ProviderQuotaUsage
        from fde_api.forward.quota import observed_plan_credits

        qsession.add(ProviderQuotaUsage(
            provider="the-odds-api", window_start=NOW, calls_used=1,
            calls_remaining=496, quota_limit=None, last_response_at=NOW))
        qsession.flush()
        assert observed_plan_credits(qsession) is None

    def test_one_real_response_corrects_it(self, qsession: Session) -> None:
        from fde_api.db.forward_models import ProviderQuotaUsage
        from fde_api.forward.quota import observed_plan_credits

        qsession.add(ProviderQuotaUsage(
            provider="the-odds-api", window_start=NOW - timedelta(days=1),
            calls_used=1, calls_remaining=496, quota_limit=None,
            last_response_at=NOW - timedelta(days=1)))
        record_usage(qsession, provider="the-odds-api",
                     headers={"x-requests-remaining": "493", "x-requests-used": "7"},
                     now=NOW)
        assert observed_plan_credits(qsession) == 500
