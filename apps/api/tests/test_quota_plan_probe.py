"""The plan can only be learned from a poll, so an unknown plan cannot forbid polling.

`QuotaConfig`'s own docstring names this failure and the numbers it happens
at: "an account with 500 credits classifies as CRITICAL at every remaining
value from 1 to 1,500, so the scheduler drops to closing-captures-only on
its first call and never comes back. The numbers all look deliberate, and
nothing reports a misconfiguration."

The fix for that was `thresholds_for(plan_credits, cfg)`, which scales the
reserve to the plan actually in force. It works — and it only engages once
the plan is KNOWN. The plan becomes known from `x-requests-remaining` plus
`x-requests-used` on a provider response, and a response only exists if
`authorize_poll` allowed the request. With the plan unknown, `classify`
falls back to the 20,000-plan absolutes, calls a 496-credit balance
CRITICAL, and refuses every poll outside two hours of kickoff.

So the system will not make the call that would tell it the thing it is
refusing over. The escape is narrow and real — a closing capture is
protected, and a game inside two hours is allowed — but between those
moments it captures nothing, which on this database means nothing at all
until 2026-08-14 22:00Z.

The state existed before this file. `TestTheUnknownPlanIsNotGuessed` pins
that a legacy row reports the plan as unknown, and that "one real response
corrects it" — by calling `record_usage` directly. Nothing asked whether
that response could ever arrive. That gap is exactly where the defect
lives.

An unknown plan is now its own state, permitted a small daily probe budget
so one response records the plan, and reported as unknown rather than as a
reason to top up an account that may be full.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ProviderQuotaUsage
from fde_api.db.models import Base
from fde_api.forward.quota import (
    QuotaConfig,
    QuotaState,
    authorize_poll,
    classify,
    observed_plan_credits,
    record_usage,
)

NOW = datetime(2026, 8, 11, 23, 0, tzinfo=UTC)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


def _legacy_rows(s: Session, *remaining: int) -> None:
    """The two rows sitting in the development database, verbatim: a
    balance recorded with no plan beside it."""
    for rem in remaining:
        s.add(ProviderQuotaUsage(
            provider="the-odds-api", window_start=NOW - timedelta(days=10),
            calls_used=1, calls_remaining=rem, quota_limit=None,
            last_response_at=NOW - timedelta(days=10),
        ))
    s.flush()


class TestAnUnknownPlanIsItsOwnState:
    def test_it_is_not_reported_as_critical(self, session: Session) -> None:
        assert classify(496, QuotaConfig(), None) == QuotaState.UNKNOWN_PLAN

    def test_a_known_plan_still_classifies_normally(self) -> None:
        """The guard against over-correcting: this must not become a way to
        ignore a genuinely spent account."""
        cfg = QuotaConfig()
        assert classify(20, cfg, 500) == QuotaState.CRITICAL
        assert classify(400, cfg, 20_000) == QuotaState.CRITICAL
        assert classify(19_000, cfg, 20_000) == QuotaState.OK

    def test_exhaustion_does_not_need_a_plan(self, session: Session) -> None:
        """`remaining <= 0` is absolute. No plan is required to know that
        nothing is left."""
        assert classify(0, QuotaConfig(), None) == QuotaState.EXHAUSTED
        assert classify(-5, QuotaConfig(), None) == QuotaState.EXHAUSTED

    def test_no_observation_at_all_is_still_OK(self) -> None:
        """Before any response, `remaining` is unknown too, and the
        pre-existing answer was OK. Unchanged."""
        assert classify(None, QuotaConfig(), None) == QuotaState.OK


class TestTheProbeIsAuthorised:
    def test_a_distant_game_is_polled_when_the_plan_is_unknown(
        self, session: Session
    ) -> None:
        """The deadlock, stated directly. Against the old rule every one of
        these was refused."""
        _legacy_rows(session, 497, 496)
        for hours in (72.0, 24.0, 6.0, 3.0):
            decision = authorize_poll(session, hours_to_kickoff=hours, now=NOW)
            assert decision.allowed is True, f"{hours}h: {decision.reason}"
            assert decision.state == QuotaState.UNKNOWN_PLAN

    def test_the_reason_says_why_it_is_polling(self, session: Session) -> None:
        _legacy_rows(session, 496)
        decision = authorize_poll(session, hours_to_kickoff=72.0, now=NOW)
        assert "unknown" in decision.reason.lower()

    def test_it_polls_at_a_reduced_cadence_not_a_full_one(
        self, session: Session
    ) -> None:
        """Permitted is not the same as unconstrained. The plan is still
        unknown while this runs."""
        _legacy_rows(session, 496)
        decision = authorize_poll(session, hours_to_kickoff=72.0, now=NOW)
        assert decision.cadence_multiplier > 1.0

    def test_the_probe_budget_is_finite(self, session: Session) -> None:
        """If the plan somehow never gets recorded, this must not become an
        unbounded licence to spend."""
        cfg = QuotaConfig()
        _legacy_rows(session, 496)
        spent = 0
        while spent < cfg.unknown_plan_probe_credits * 3:
            session.add(ProviderQuotaUsage(
                provider="the-odds-api", window_start=NOW, calls_used=1,
                calls_remaining=496, quota_limit=None, last_response_at=NOW,
            ))
            spent += cfg.credits_per_request
        session.flush()
        decision = authorize_poll(session, hours_to_kickoff=72.0, now=NOW)
        assert decision.allowed is False
        assert "probe" in decision.reason.lower()

    def test_an_exhausted_account_is_still_refused(self, session: Session) -> None:
        _legacy_rows(session, 0)
        decision = authorize_poll(session, hours_to_kickoff=72.0, now=NOW)
        assert decision.allowed is False
        assert decision.state == QuotaState.EXHAUSTED


class TestTheProbeEndsTheUnknownState:
    def test_one_authorised_poll_makes_the_plan_known(self, session: Session) -> None:
        """The whole point, end to end: authorise, record the response, and
        the guess is replaced by an observation."""
        _legacy_rows(session, 496)
        assert observed_plan_credits(session) is None
        assert authorize_poll(session, hours_to_kickoff=72.0, now=NOW).allowed

        record_usage(
            session, provider="the-odds-api",
            headers={"x-requests-remaining": "493", "x-requests-used": "7"},
            now=NOW,
        )
        assert observed_plan_credits(session) == 500

    def test_and_the_state_then_reflects_the_real_plan(self, session: Session) -> None:
        _legacy_rows(session, 496)
        record_usage(
            session, provider="the-odds-api",
            headers={"x-requests-remaining": "493", "x-requests-used": "7"},
            now=NOW,
        )
        decision = authorize_poll(session, hours_to_kickoff=72.0, now=NOW)
        assert decision.state == QuotaState.OK, decision.reason


class TestOnlyOneThingWritesTheBudget:
    def test_capture_odds_does_not_record_quota_usage(self, session: Session) -> None:
        """Two writers, one lossy, is how the plan-less rows got there.

        `capture_odds` took a `quota_remaining` argument and, when given
        one, wrote a `ProviderQuotaUsage` row with `quota_limit=None` —
        recording a balance while discarding the plan that makes it
        meaningful. `record_usage` sits beside it and derives the plan from
        both headers, which is the only place the headers exist.

        No caller ever passed it, in production or in tests, so the branch
        was unreachable — but it is the shape of the two rows in the
        development database, and it is the one thing in the codebase that
        could manufacture more of them.
        """
        from fde_api.forward.cohort import ProviderMode
        from fde_api.forward.modes import DataMode
        from fde_api.forward.odds import capture_odds

        capture_odds(
            session, [], provider_mode=ProviderMode.FIXTURE,
            data_mode=DataMode.DEMO, observed_at=NOW,
        )
        assert session.query(ProviderQuotaUsage).count() == 0

    def test_it_takes_no_quota_argument_at_all(self) -> None:
        """Stated against the signature, so the branch cannot come back by
        someone re-adding the parameter and wiring it up."""
        import inspect

        from fde_api.forward.odds import capture_odds

        assert "quota_remaining" not in inspect.signature(capture_odds).parameters


class TestHealthReportsTheUnknownPlanHonestly:
    def _check(self, session: Session):
        from fde_api.forward.health import run_health_checks

        for check in run_health_checks(session, now=NOW)["checks"]:
            if check["id"] == "provider_quota":
                return check
        raise AssertionError("provider_quota")

    def test_it_does_not_advise_topping_up_an_unmeasured_plan(
        self, session: Session
    ) -> None:
        """"Top up the provider plan" is advice about an account whose size
        is not known. It may hold twenty thousand credits."""
        _legacy_rows(session, 496)
        assert "top up" not in self._check(session)["remediation"].lower()

    def test_it_says_the_plan_is_unmeasured(self, session: Session) -> None:
        _legacy_rows(session, 496)
        check = self._check(session)
        assert "unknown" in (check["explanation"] + check["remediation"]).lower()

    def test_a_real_shortage_still_advises_topping_up(self, session: Session) -> None:
        """The guard: this must not have quieted the message for the case
        it was written for."""
        record_usage(
            session, provider="the-odds-api",
            headers={"x-requests-remaining": "20", "x-requests-used": "480"},
            now=NOW,
        )
        assert "top up" in self._check(session)["remediation"].lower()
