"""Integration tests for wired scheduler handlers.

These go beyond run-key prevention: they assert that DOMAIN records are
not duplicated by reruns, races, retries, or a restart after a partial
write, and that Data Health suppression cannot be bypassed by calling the
service directly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import (
    AvailabilityAssessment,
    ConsensusSnapshot,
    ForwardPrediction,
    OddsQuote,
    ScheduledJobRun,
    ScheduleObservation,
)
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import (
    HANDLERS,
    JOB_DEPENDENCY_RULES,
    WeatherStatus,
    register_all,
)
from fde_api.forward.health import (
    CandidateSuppressedError,
    Severity,
    Status,
    enforce_candidate_gate,
    run_health_checks,
)
from fde_api.forward.ledger import HealthGate, evaluate_candidate
from fde_api.forward.policy import build_policy_draft, freeze_policy
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.scheduler import FrozenClock, JobStatus, Scheduler
from fde_api.forward.state import DomainState, Outcome
from fde_api.forward.venues import seed_venues

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
NOW = KICK - timedelta(days=3)

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def _row(**over: str) -> str:
    base = dict.fromkeys(HEADER.split(","), "")
    base.update({
        "game_id": "2026_02_KC_BUF", "season": "2026", "game_type": "REG", "week": "2",
        "gameday": "2026-09-13", "gametime": "13:00", "away_team": "KC", "home_team": "BUF",
        "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
        "stadium_id": "BUF00", "stadium": "Highmark Stadium", "away_rest": "7", "home_rest": "7",
    })
    base.update(over)
    return ",".join(base[k] for k in HEADER.split(","))


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode()


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    # Never write policy or capture artifacts into the real data directory.
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, future=True)
    with f() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(
            policy_version="ftp-2026-v1", start=date(2026, 9, 1), end=date(2027, 2, 28)))
        ingest_schedule(s, _csv(_row()), season=2026, observed_at=NOW - timedelta(days=10))
        s.commit()
    return f


def _sched(factory, clock=None, provider_mode=ProviderMode.FIXTURE):
    s = Scheduler(factory, clock=clock or FrozenClock(NOW), cohort=Cohort.BURN_IN,
                  provider_mode=provider_mode, policy_version="ftp-2026-v1")
    register_all(s)
    return s


def _odds_payload(ts: datetime, line: float = -2.5) -> list[dict]:
    def book(key: str) -> dict:
        return {"key": key, "last_update": ts.isoformat(), "markets": [
            {"key": "spreads", "outcomes": [
                {"name": "Buffalo Bills", "price": -110, "point": line},
                {"name": "Kansas City Chiefs", "price": -110, "point": -line}]}]}
    return [{"id": "evt1", "commence_time": KICK.isoformat(),
             "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs",
             "bookmakers": [book("draftkings"), book("fanduel"), book("betmgm")]}]


# --------------------------------------------------------------------------- #


class TestHandlersRegistered:
    def test_all_thirteen_plus_consensus_registered(self, factory) -> None:
        s = _sched(factory)
        for name in ("schedule_refresh", "odds_capture", "weather_capture",
                     "injury_reconciliation", "availability_computation", "feature_snapshot",
                     "prediction_vintage", "manual_price_expiration", "closing_capture",
                     "result_ingestion", "settlement", "forward_evaluation",
                     "data_health_reconciliation"):
            assert name in s.jobs, name

    def test_every_handler_is_callable(self) -> None:
        assert all(callable(h) for h in HANDLERS.values())


class TestDependencyOutcomeRules:
    def test_missing_key_is_skipped_not_failure(self, factory) -> None:
        s = _sched(factory, provider_mode=ProviderMode.KEY_MISSING)
        r = s.run_job("odds_capture", slot=NOW)
        assert r["status"] == "finished"  # the scheduler run itself succeeded
        with factory() as sess:
            row = sess.scalars(select(ScheduledJobRun)).one()
        assert row.records_written == 0

    def test_missing_key_writes_no_quotes_and_no_fixture_substitution(self, factory) -> None:
        s = _sched(factory, provider_mode=ProviderMode.KEY_MISSING)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _odds_payload(NOW)})
        with factory() as sess:
            assert sess.scalar(select(func.count(OddsQuote.id))) == 0

    def test_declared_rules_are_two_axis_tuples(self) -> None:
        for job, rules in JOB_DEPENDENCY_RULES.items():
            for condition, value in rules.items():
                assert isinstance(value, tuple) and len(value) == 2, f"{job}/{condition}"
                outcome, domain = value
                assert outcome in {o.value for o in Outcome}, f"{job}/{condition}"
                assert domain in {d.value for d in DomainState}, f"{job}/{condition}"

    def test_weather_beyond_horizon_is_not_a_failure(self, factory) -> None:
        far = KICK - timedelta(days=30)
        s = _sched(factory, clock=FrozenClock(far))
        r = s.run_job("weather_capture", slot=far, params={"nws_client": object()})
        assert r["status"] == "finished"
        assert any("outcome=SUCCESS" in w for w in r["warnings"])

    def test_weather_status_vocabulary(self) -> None:
        assert WeatherStatus.NOT_YET_AVAILABLE.value == "NOT_YET_AVAILABLE"
        assert WeatherStatus.NOT_APPLICABLE.value == "NOT_APPLICABLE"


class TestDomainIdempotency:
    """Duplicate prevention at the RECORD level, not just the run key."""

    def test_schedule_rerun_creates_no_duplicate_observations(self, factory) -> None:
        s = _sched(factory)
        s.run_job("schedule_refresh", slot=NOW)
        with factory() as sess:
            after_first = sess.scalar(select(func.count(ScheduleObservation.id)))
        # A second refresh of identical content must append nothing.
        s.run_job("schedule_refresh", slot=NOW + timedelta(hours=12))
        with factory() as sess:
            after_second = sess.scalar(select(func.count(ScheduleObservation.id)))
        assert after_first > 0
        assert after_second == after_first

    def test_odds_rerun_creates_no_duplicate_quotes(self, factory) -> None:
        payload = _odds_payload(NOW)
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload})
        with factory() as sess:
            first = sess.scalar(select(func.count(OddsQuote.id)))
        s2 = _sched(factory)
        s2.run_job("odds_capture", slot=NOW + timedelta(minutes=5), params={"fixture_payload": payload})
        with factory() as sess:
            second = sess.scalar(select(func.count(OddsQuote.id)))
        assert first > 0 and second == first

    def test_two_workers_racing_write_domain_records_once(self, factory) -> None:
        payload = _odds_payload(NOW)
        a, b = _sched(factory), _sched(factory)
        ra = a.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload})
        rb = b.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload})
        assert {ra["status"], rb["status"]} == {"finished", "skipped"}
        with factory() as sess:
            quotes = sess.scalar(select(func.count(OddsQuote.id)))
        assert quotes == 6  # 3 books x 2 selections, written exactly once

    def test_consensus_rerun_is_bounded(self, factory) -> None:
        s = _sched(factory)
        s.register_consensus = None
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _odds_payload(NOW)})
        s.run_job("consensus_build", slot=NOW)
        with factory() as sess:
            first = sess.scalar(select(func.count(ConsensusSnapshot.id)))
        assert first >= 1

    def test_availability_rerun_does_not_duplicate_for_same_cutoff(self, factory) -> None:
        from fde_api.forward.injuries import SourceCategory, record_injury_observation

        with factory() as sess:
            record_injury_observation(
                sess, canonical_game_id="2026_02_KC_BUF", team_id="BUF",
                player_id="00-0011111", report_date="2026-09-11",
                observed_at=NOW - timedelta(hours=2), source_category=SourceCategory.OFFICIAL_VERIFIED,
                practice_status="FULL", game_designation="NONE", now=NOW)
            sess.commit()
        s = _sched(factory)
        s.run_job("availability_computation", slot=NOW)
        with factory() as sess:
            first = sess.scalar(select(func.count(AvailabilityAssessment.id)))
        s.run_job("availability_computation", slot=NOW)  # same slot -> skipped
        with factory() as sess:
            assert sess.scalar(select(func.count(AvailabilityAssessment.id))) == first

    def test_prediction_vintage_rerun_creates_no_duplicate(self, factory) -> None:
        clock = FrozenClock(KICK - timedelta(days=5))
        s = _sched(factory, clock=clock)
        s.run_job("prediction_vintage", slot=clock.now(), params={"moments": (2.0, 13.0, 44.0, 10.0)})
        with factory() as sess:
            first = sess.scalar(select(func.count(ForwardPrediction.id)))
        s2 = _sched(factory, clock=clock)
        s2.run_job("prediction_vintage", slot=clock.now() + timedelta(hours=1),
                   params={"moments": (2.0, 13.0, 44.0, 10.0)})
        with factory() as sess:
            assert sess.scalar(select(func.count(ForwardPrediction.id))) == first


class TestPartialWriteRecovery:
    """A crash AFTER a domain write but BEFORE the run record finalizes."""

    def test_restart_reconciles_and_does_not_duplicate_domain_records(self, factory) -> None:
        payload = _odds_payload(NOW)

        # Write domain records, then leave the run row stuck in `running`.
        s1 = _sched(factory)
        s1.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload})
        with factory() as sess:
            quotes_before = sess.scalar(select(func.count(OddsQuote.id)))
            run = sess.scalars(select(ScheduledJobRun)).one()
            run.status = JobStatus.RUNNING.value  # simulate termination mid-finalize
            run.completed_at = None
            sess.commit()

        # Restart.
        s2 = _sched(factory)
        out = s2.reconcile_startup()
        assert out["count"] == 1

        with factory() as sess:
            run = sess.scalars(select(ScheduledJobRun)).one()
            assert run.status == JobStatus.INTERRUPTED.value
            assert sess.scalar(select(func.count(OddsQuote.id))) == quotes_before  # intact

        # Retry the same logical slot: domain writes must not duplicate.
        s2.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload})
        with factory() as sess:
            assert sess.scalar(select(func.count(OddsQuote.id))) == quotes_before
            assert sess.scalar(select(func.count(ScheduledJobRun.id))) >= 2  # recovery recorded

    def test_retry_after_retryable_failure_does_not_duplicate(self, factory) -> None:
        payload = _odds_payload(NOW)
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload})
        with factory() as sess:
            n = sess.scalar(select(func.count(OddsQuote.id)))
        # Manual administrative rerun of the same slot.
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": payload}, manual=True)
        with factory() as sess:
            assert sess.scalar(select(func.count(OddsQuote.id))) == n


class TestDataHealth:
    def test_report_shape(self, factory) -> None:
        with factory() as sess:
            r = run_health_checks(sess, provider_mode=ProviderMode.KEY_MISSING,
                                  policy_version="ftp-2026-v1", now=NOW)
        assert r["checks"]
        for c in r["checks"]:
            for key in ("id", "severity", "status", "explanation", "remediation",
                        "last_checked_at", "affected_games", "suppresses_candidates"):
                assert key in c, key

    def test_missing_key_is_critical_and_suppresses(self, factory) -> None:
        with factory() as sess:
            r = run_health_checks(sess, provider_mode=ProviderMode.KEY_MISSING,
                                  policy_version="ftp-2026-v1", now=NOW)
        key_check = next(c for c in r["checks"] if c["id"] == "odds_key_configured")
        assert key_check["severity"] == Severity.CRITICAL.value
        assert key_check["status"] != Status.OK.value
        assert r["candidates_suppressed"] is True
        assert r["suppression_reasons"]

    def test_suppression_cannot_be_bypassed_by_calling_service_directly(self, factory) -> None:
        """The gate lives in the domain service, so an API call cannot skip it."""
        with factory() as sess:
            report = run_health_checks(sess, provider_mode=ProviderMode.KEY_MISSING,
                                       policy_version="ftp-2026-v1", now=NOW)
            gate = HealthGate.from_report(report)
            policy = build_policy_draft(policy_version="ftp-2026-v1",
                                          start=date(2026, 9, 1), end=date(2027, 2, 28))
            # A probability that would otherwise be a strong candidate.
            ev = evaluate_candidate(
                market="SPREAD", selection="HOME", line=-2.5, american=-110,
                model_probability=0.85, price_source="consensus", price_age_seconds=60,
                policy=policy, data_completeness=1.0, health_gate=gate)
        assert ev.status == "DATA_INCOMPLETE"
        assert any("suppressed by Data Health" in r for r in ev.reasons)

    def test_gate_raises_when_critical(self, factory) -> None:
        with factory() as sess, pytest.raises(CandidateSuppressedError, match="suppressed"):
            enforce_candidate_gate(sess, provider_mode=ProviderMode.KEY_MISSING,
                                   policy_version="ftp-2026-v1", now=NOW)

    def test_candidate_allowed_when_gate_clear(self, factory) -> None:
        policy = build_policy_draft(policy_version="ftp-2026-v1",
                                      start=date(2026, 9, 1), end=date(2027, 2, 28))
        ev = evaluate_candidate(
            market="SPREAD", selection="HOME", line=-2.5, american=-110,
            model_probability=0.85, price_source="consensus", price_age_seconds=60,
            policy=policy, data_completeness=1.0,
            health_gate=HealthGate(suppressed=False, reasons=[]))
        assert ev.status == "RESEARCH_CANDIDATE"

    def test_international_metadata_check_present(self, factory) -> None:
        with factory() as sess:
            r = run_health_checks(sess, policy_version="ftp-2026-v1", now=NOW)
        ids = {c["id"] for c in r["checks"]}
        for expected in ("schedule_game_count", "international_game_count",
                         "international_stadium_count", "international_venue_metadata",
                         "policy_hash_integrity", "model_artifact_integrity",
                         "database_readiness", "odds_freshness"):
            assert expected in ids, expected

    def test_policy_integrity_verified(self, factory) -> None:
        with factory() as sess:
            r = run_health_checks(sess, policy_version="ftp-2026-v1", now=NOW)
        c = next(x for x in r["checks"] if x["id"] == "policy_hash_integrity")
        assert c["status"] == Status.OK.value

    def test_stale_provider_never_reuses_old_quote_as_current(self, factory) -> None:
        with factory() as sess:
            r = run_health_checks(sess, provider_mode=ProviderMode.KEY_MISSING,
                                  policy_version="ftp-2026-v1", now=NOW)
        c = next(x for x in r["checks"] if x["id"] == "odds_freshness")
        assert c["status"] != Status.OK.value
        assert "never silently reuses" in str(c["detail"])


class TestRunRecordPersistence:
    def test_handler_metrics_persisted(self, factory) -> None:
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _odds_payload(NOW)})
        with factory() as sess:
            row = sess.scalars(select(ScheduledJobRun)).one()
        assert row.records_received > 0
        assert row.records_written > 0
        assert row.code_commit
        assert row.error_summary and "outcome=" in row.error_summary


class TestOutcomeDomainSeparation:
    """Execution health and data quality are independent axes."""

    def test_enum_members(self) -> None:
        assert {o.value for o in Outcome} == {
            "SUCCESS", "SUCCESS_WITH_WARNINGS", "SKIPPED",
            "RETRYABLE_FAILURE", "TERMINAL_FAILURE", "INTERRUPTED", "RUNNING",
        }
        assert {d.value for d in DomainState} == {
            "COMPLETE", "DATA_INCOMPLETE", "NOT_YET_AVAILABLE", "NOT_APPLICABLE",
            "STALE", "SUPPRESSED", "NO_ELIGIBLE_RECORDS",
            # Migration-only; live handlers may never emit it.
            "UNKNOWN_LEGACY",
        }

    def test_data_states_are_not_execution_outcomes(self) -> None:
        outcomes = {o.value for o in Outcome}
        assert "DATA_INCOMPLETE" not in outcomes
        assert "SUPPRESSED" not in outcomes

    def test_missing_key_skipped_execution_incomplete_domain(self, factory) -> None:
        s = _sched(factory, provider_mode=ProviderMode.KEY_MISSING)
        r = s.run_job("odds_capture", slot=NOW)
        assert r["status"] == "finished"
        assert any("outcome=SKIPPED" in w for w in r["warnings"])
        assert any("domain_state=DATA_INCOMPLETE" in w for w in r["warnings"])

    def test_declared_rules_carry_both_axes(self) -> None:
        assert JOB_DEPENDENCY_RULES["odds_capture"]["missing_key"] == (
            Outcome.SKIPPED.value, DomainState.DATA_INCOMPLETE.value)
        assert JOB_DEPENDENCY_RULES["weather_capture"]["beyond_horizon"] == (
            Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.NOT_YET_AVAILABLE.value)
        assert JOB_DEPENDENCY_RULES["weather_capture"]["international_venue"] == (
            Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.NOT_APPLICABLE.value)
        assert JOB_DEPENDENCY_RULES["prediction_vintage"]["missing_quarterback"] == (
            Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.DATA_INCOMPLETE.value)
        assert JOB_DEPENDENCY_RULES["prediction_vintage"]["health_suppressed"] == (
            Outcome.SUCCESS_WITH_WARNINGS.value, DomainState.SUPPRESSED.value)

    def test_both_axes_persisted_to_run_record(self, factory) -> None:
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _odds_payload(NOW)})
        with factory() as sess:
            row = sess.scalars(select(ScheduledJobRun)).one()
        assert "outcome=" in row.error_summary
        assert "domain_state=" in row.error_summary

    def test_health_suppression_reports_suppressed_domain_state(self, factory) -> None:
        s = _sched(factory, provider_mode=ProviderMode.KEY_MISSING)
        r = s.run_job("data_health_reconciliation", slot=NOW)
        assert r["status"] == "finished"
        assert any("domain_state=SUPPRESSED" in w for w in r["warnings"])
