"""What a season of scheduling does, rather than what one slot does.

`tests/test_scheduler.py` covers a slot thoroughly: idempotency, retries,
restart recovery, concurrent claims. Every one of those runs two or three
slots. A forward test runs the scheduler continuously from 1 September to
the end of February — roughly 26,000 slots for the five-minute jobs — and
a whole class of defect only appears at that length.

This codebase has already been bitten by exactly one of them. `record_usage`
carries the comment that `calls_used` must be THIS call's requests rather
than the provider's month-to-date counter, because `credits_used_since`
SUMS the column: "summing a cumulative counter grows quadratically: after n
polls it reports 1+2+...+n requests instead of n, so the daily ceiling
slams shut after a handful of calls and blames a budget that was never
spent." That is invisible at three slots and fatal at three hundred.

`scheduler_running` has never fired in this deployment — `scheduled_job_runs`
is empty — and the scheduler REFUSES to run outside the policy window, so
1 September is otherwise the first time any of this is exercised, on the
day it starts mattering.

The properties below are the ones that hold at n=3 whether or not the code
is right, and stop holding at n=200 if it is not.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import ProviderQuotaUsage, ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.recovery import IDEMPOTENCY_KEY_MAX
from fde_api.forward.scheduler import (
    FrozenClock,
    JobDefinition,
    JobResult,
    Scheduler,
)

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
INTERVAL = timedelta(minutes=5)

# Long enough that a quadratic or per-pass-append defect is unmissable, short
# enough to stay a unit test. At n=200 a triangular sum is 20,100 against a
# true 200 — two orders of magnitude apart.
PASSES = 200


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _sched(factory, clock):
    return Scheduler(
        factory, clock=clock, cohort=Cohort.BURN_IN,
        provider_mode=ProviderMode.FIXTURE, policy_version="ftp-2026-v1",
        rng=random.Random(1),
    )


def _run_for(factory, *, passes: int = PASSES, jobs: int = 2, handler=None):
    """Drive `passes` distinct slots, one tick per slot."""
    clock = FrozenClock(T0)
    s = _sched(factory, clock)
    for i in range(jobs):
        s.register(JobDefinition(
            name=f"job{i}", handler=handler or (lambda ctx: JobResult()),
            interval=INTERVAL,
        ))
    for _ in range(passes):
        s.tick()
        clock.advance(INTERVAL)
    return s, clock


class TestStateGrowsWithTheWorkAndNotFasterThanIt:
    def test_one_run_row_per_slot_per_job(self, factory) -> None:
        """Linear. A per-pass append, a re-run of a settled slot, or a
        retry that forgets it already claimed the slot all show up here as
        a count above `passes * jobs`."""
        _run_for(factory)
        with factory() as s:
            assert s.scalar(select(func.count(ScheduledJobRun.id))) == PASSES * 2

    def test_every_slot_ends_with_exactly_one_run(self, factory) -> None:
        _run_for(factory)
        with factory() as s:
            per_slot = s.execute(
                select(ScheduledJobRun.job_kind, ScheduledJobRun.scheduled_for,
                       func.count(ScheduledJobRun.id))
                .group_by(ScheduledJobRun.job_kind, ScheduledJobRun.scheduled_for)
            ).all()
        assert len(per_slot) == PASSES * 2
        assert {n for _, _, n in per_slot} == {1}

    def test_replaying_the_whole_run_adds_nothing(self, factory) -> None:
        """Idempotency over the whole history, not one slot of it. A
        restart that re-ticks every slot it already did must be free."""
        _run_for(factory)
        with factory() as s:
            before = s.scalar(select(func.count(ScheduledJobRun.id)))
        _run_for(factory)
        with factory() as s:
            assert s.scalar(select(func.count(ScheduledJobRun.id))) == before


class TestTheCreditAccountingStaysLinear:
    """The defect the codebase already carries a comment about.

    `credits_used_since` sums `calls_used`. If anything ever writes the
    provider's cumulative month-to-date counter into that column instead of
    this call's cost, the sum becomes triangular and the daily ceiling
    closes on a budget nobody spent.
    """

    def test_n_polls_report_n_calls_not_n_squared(self, factory) -> None:
        from fde_api.forward.quota import credits_used_since, record_usage

        with factory() as s:
            for i in range(PASSES):
                record_usage(
                    s, provider="the-odds-api",
                    headers={"x-requests-remaining": str(20_000 - i - 1),
                             "x-requests-used": str(i + 1)},
                    now=T0 + timedelta(minutes=i),
                )
            s.commit()
            used = credits_used_since(s, "the-odds-api", T0 - timedelta(days=1))

        assert used == PASSES, f"expected {PASSES} calls, got {used}"
        # The shape of the failure, named so a regression is recognisable.
        assert used != PASSES * (PASSES + 1) // 2, "the sum went triangular"

    def test_the_observed_plan_does_not_drift_over_a_long_run(
        self, factory
    ) -> None:
        """`observed_plan_credits` takes the MAX recorded limit. Across two
        hundred responses whose remaining count falls steadily, the plan it
        reports must stay the plan."""
        from fde_api.forward.quota import observed_plan_credits, record_usage

        with factory() as s:
            for i in range(PASSES):
                record_usage(
                    s, provider="the-odds-api",
                    headers={"x-requests-remaining": str(20_000 - i - 1),
                             "x-requests-used": str(i + 1)},
                    now=T0 + timedelta(minutes=i),
                )
            s.commit()
            assert observed_plan_credits(s) == 20_000


class TestIdentifiersStayInsideTheirColumn:
    def test_every_idempotency_key_fits_across_the_whole_run(
        self, factory
    ) -> None:
        """`idempotency_key` is VARCHAR(160) and the slot is part of it. A
        key that overflows only once the slot string lengthens is exactly
        the kind of thing three slots cannot show."""
        _run_for(factory)
        with factory() as s:
            keys = list(s.scalars(select(ScheduledJobRun.idempotency_key)))
        assert len(keys) == PASSES * 2
        longest = max(keys, key=len)
        assert len(longest) <= IDEMPOTENCY_KEY_MAX, longest

    def test_no_two_slots_share_a_key(self, factory) -> None:
        """The claim the UNIQUE constraint rests on. A collision would make
        the second slot look already-completed and silently skip its work."""
        _run_for(factory)
        with factory() as s:
            keys = list(s.scalars(select(ScheduledJobRun.idempotency_key)))
        assert len(set(keys)) == len(keys)


class TestAFailingJobDoesNotPoisonTheRest:
    """Retries are a LINEAGE CHAIN, not attempts inside one record.

    Written expecting one row per slot, which was wrong: a failing slot
    leaves four, and that is the design. `recovery.py` models a retry as a
    successor run carrying `root_run_id`, `recovery_of_run_id` and
    `recovery_sequence`, so the history of an attempt survives instead of
    being overwritten by the attempt that followed it.

    The invariant that matters over a long run is therefore not "one row"
    but the two `lineage_*` health checks: the chain per slot is BOUNDED,
    and at most one member of it is RUNNING.
    """

    @staticmethod
    def _always_fails(ctx):
        raise RuntimeError("provider down")

    def test_the_retry_chain_per_slot_is_bounded(self, factory) -> None:
        """Unbounded retries are how one dead provider turns into a table
        that grows until the disk does."""
        _run_for(factory, passes=50, jobs=1, handler=self._always_fails)
        with factory() as s:
            per_slot = s.execute(
                select(ScheduledJobRun.scheduled_for, func.count(ScheduledJobRun.id))
                .group_by(ScheduledJobRun.scheduled_for)
            ).all()
        assert len(per_slot) == 50
        sizes = {n for _, n in per_slot}
        assert len(sizes) == 1, f"the chain length varies by slot: {sorted(sizes)}"
        assert sizes.pop() <= 8, "the retry chain is not bounded"

    def test_the_chain_never_becomes_structurally_corrupt(
        self, factory
    ) -> None:
        """No CRITICAL lineage violation across fifty failing slots.

        The CRITICAL set is the structural one — two RUNNING members of a
        chain, a broken predecessor link, a branch, a sequence with a gap,
        a root that is not sequence zero. Any of them means the record of
        what ran can no longer be reconstructed, and
        `blocks_automatic_recovery` uses exactly this predicate.

        Asserted on severity rather than on an empty list: every slot here
        also reports `replay_decision_absent`, a WARNING, because a
        synthetic handler that always raises never records the operator
        decision a real recovery would. That is the fixture's shape, not a
        defect, and demanding an empty list would have pinned it as one.
        """
        from fde_api.forward.recovery import lineage_violations

        _run_for(factory, passes=50, jobs=1, handler=self._always_fails)
        with factory() as s:
            problems = lineage_violations(s)

        critical = [p for p in problems if p["severity"] == "CRITICAL"]
        assert critical == [], critical
        assert {p["check"] for p in problems} <= {"replay_decision_absent"}, (
            "an unexpected lineage warning appeared over a long run"
        )

    def test_the_failures_are_recorded_rather_than_swallowed(
        self, factory
    ) -> None:
        _run_for(factory, passes=50, jobs=1, handler=self._always_fails)
        with factory() as s:
            statuses = set(s.scalars(select(ScheduledJobRun.status)))
        assert statuses != {"finished"}, "a failing job reported success"


class TestNothingElseAccumulates:
    def test_a_clean_run_writes_no_quota_rows(self, factory) -> None:
        """The scheduler's own bookkeeping must not leave provider budget
        records behind. Only a real provider response may write one."""
        _run_for(factory)
        with factory() as s:
            assert s.scalar(select(func.count(ProviderQuotaUsage.id))) == 0
