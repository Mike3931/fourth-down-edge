"""The runner's refusals.

The runner is the only way to start the scheduler outside a test, which
makes it the one place where a careless default becomes a permanent,
wrong record. Three things it must refuse, and one it must not:

  refuse   a closed policy window - captured records would belong to no
           evaluation cohort
  refuse   FIXTURE data inside LIVE_RESEARCH - fixture output must never
           be stored as live-provider output
  refuse   LIVE provider mode with no key - "no key" must never be
           mistaken for "no prices"

  allow    a dry run, always. Being unable to LOOK at what is due because
           a real run would have been refused is the kind of strictness
           that gets worked around instead of respected.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fde_api.db.models import Base

RUNNER = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_scheduler.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_scheduler", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**over) -> argparse.Namespace:
    base = {
        "cohort": "burn_in", "provider_mode": "FIXTURE", "data_mode": "DEMO",
        "policy_version": None, "allow_closed_window": False,
        "allow_fixture_in_live_research": False, "once": True, "dry_run": False,
        "interval": 60.0, "max_passes": 0,
    }
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture()
def runner():
    return _runner()


class TestTheRunnerRefusesToRecordSomethingUntrue:
    def test_a_closed_policy_window_stops_the_run(self, runner, factory) -> None:
        problems = runner._preflight(_args(), factory)
        assert any("no forward-test policy is in force" in p for p in problems)

    def test_the_closed_window_can_be_overridden_explicitly(self, runner, factory) -> None:
        """A capture-only rehearsal is legitimate - but it has to be asked
        for, so nobody later mistakes it for forward-test evidence."""
        problems = runner._preflight(_args(allow_closed_window=True), factory)
        assert not any("policy is in force" in p for p in problems)

    def test_fixture_data_in_live_research_stops_the_run(self, runner, factory) -> None:
        problems = runner._preflight(
            _args(data_mode="LIVE_RESEARCH", provider_mode="FIXTURE",
                  allow_closed_window=True), factory)
        assert any("fixture output inside the live-research cohort" in p
                   for p in problems)

    def test_fixture_in_live_research_can_be_asked_for_by_name(
        self, runner, factory
    ) -> None:
        problems = runner._preflight(
            _args(data_mode="LIVE_RESEARCH", provider_mode="FIXTURE",
                  allow_closed_window=True, allow_fixture_in_live_research=True),
            factory)
        assert not any("live-research cohort" in p for p in problems)

    def test_demo_data_mode_needs_no_override(self, runner, factory) -> None:
        problems = runner._preflight(
            _args(data_mode="DEMO", provider_mode="FIXTURE",
                  allow_closed_window=True), factory)
        assert problems == []

    def test_live_provider_without_a_key_stops_the_run(
        self, runner, factory, monkeypatch
    ) -> None:
        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        problems = runner._preflight(
            _args(provider_mode="LIVE", allow_closed_window=True), factory)
        assert any("FDE_ODDS_API_KEY is not set" in p for p in problems)

    def test_a_blank_key_counts_as_absent(
        self, runner, factory, monkeypatch
    ) -> None:
        """An empty string is what a misconfigured environment file yields,
        and it must not read as configured."""
        monkeypatch.setenv("FDE_ODDS_API_KEY", "   ")
        problems = runner._preflight(
            _args(provider_mode="LIVE", allow_closed_window=True), factory)
        assert any("FDE_ODDS_API_KEY is not set" in p for p in problems)

    def test_live_with_a_key_is_allowed(
        self, runner, factory, monkeypatch
    ) -> None:
        monkeypatch.setenv("FDE_ODDS_API_KEY", "test-value-never-logged")
        problems = runner._preflight(
            _args(provider_mode="LIVE", allow_closed_window=True), factory)
        assert problems == []


class TestTheRunnerNeverHandlesTheKey:
    def test_the_key_is_not_an_argument(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        assert "--api-key" not in source
        assert "--odds-key" not in source

    def test_the_key_value_is_never_printed(self) -> None:
        """It reads the variable only to ask whether it is set."""
        source = RUNNER.read_text(encoding="utf-8")
        for line in source.splitlines():
            if "FDE_ODDS_API_KEY" not in line or line.strip().startswith("#"):
                continue
            assert "print" not in line and "log." not in line, line


class TestTheSummaryReportsWhatActuallyHappened:
    def test_it_counts_the_status_key_run_job_returns(self, runner) -> None:
        """Reading a key that is not there yields a tidy tally of UNKNOWNs -
        a summary that is confidently wrong rather than absent."""
        results = [{"status": "finished"}, {"status": "skipped"},
                   {"status": "finished"}]
        assert runner._summarise(results) == {"finished": 2, "skipped": 1}

    def test_a_missing_status_is_named_not_silently_bucketed(self, runner) -> None:
        assert runner._summarise([{}]) == {"MISSING_STATUS": 1}


class TestTheJobsAreActuallyRegistered:
    def test_every_handler_is_registered(self, runner, factory) -> None:
        from fde_api.forward.handlers import HANDLERS

        scheduler, _ = runner._build(_args())
        assert set(scheduler.jobs) == set(HANDLERS)

    def test_a_dry_run_exits_zero_even_when_a_real_run_would_refuse(
        self, runner, monkeypatch, capsys
    ) -> None:
        """It writes nothing, so refusing to let it look would only teach
        people to pass the override flags by reflex.

        Driven through main() rather than the helpers, because the ordering
        of the dry-run branch against the preflight is the whole point and
        only main() has an ordering.
        """
        monkeypatch.setattr(
            sys, "argv",
            ["run_scheduler.py", "--once", "--dry-run", "--data-mode", "LIVE_RESEARCH"],
        )
        assert runner.main() == 0
        out = capsys.readouterr().out
        assert "would refuse to run" in out, "the refusals must still be reported"
        assert "nothing was executed" in out

    def test_a_real_run_in_the_same_state_refuses(
        self, runner, monkeypatch, capsys
    ) -> None:
        """The other half of the pair: same flags without --dry-run stop."""
        monkeypatch.setattr(
            sys, "argv",
            ["run_scheduler.py", "--once", "--data-mode", "LIVE_RESEARCH"],
        )
        assert runner.main() == 2
        assert "REFUSING TO RUN" in capsys.readouterr().out
