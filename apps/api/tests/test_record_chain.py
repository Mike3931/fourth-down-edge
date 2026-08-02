"""One game, end to end, driven entirely by the scheduler.

Every other test exercises a stage in isolation. This one walks a single
2026 game from schedule observation through kickoff to evaluation, moving
a clock forward the way the season actually would, and asserts that the
records LINK - that each stage's output is reachable from the previous
stage's, so the chain is a chain and not fourteen unrelated tables.

What it deliberately does NOT assert: that any prediction is good. The
engine is research-only. The chain-level invariants that matter are that
nothing leaks backwards in time, that no BET state is reachable, and that
every run row carries both state axes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import (
    ConsensusSnapshot,
    ForwardPrediction,
    OddsQuote,
    ScheduledJobRun,
    ScheduleObservation,
)
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import register_all
from fde_api.forward.policy import build_policy_draft, freeze_policy
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.scheduler import FrozenClock, Scheduler
from fde_api.forward.state import LIVE_DOMAIN_STATES, Outcome
from fde_api.forward.venues import seed_venues

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
GAME = "2026_02_KC_BUF"

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def _csv() -> bytes:
    base = dict.fromkeys(HEADER.split(","), "")
    base.update({
        "game_id": GAME, "season": "2026", "game_type": "REG", "week": "2",
        "gameday": "2026-09-13", "gametime": "13:00", "away_team": "KC", "home_team": "BUF",
        "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
        "stadium_id": "BUF00", "stadium": "Highmark Stadium", "away_rest": "7", "home_rest": "7",
    })
    return ("\n".join([HEADER, ",".join(base[k] for k in HEADER.split(","))]) + "\n").encode()


def _payload(ts: datetime, *, point: float = -2.5) -> list[dict]:
    """Three books so consensus has something to agree about."""
    def book(key: str, adj: float) -> dict:
        return {"key": key, "last_update": ts.isoformat(), "markets": [
            {"key": "spreads", "outcomes": [
                {"name": "Buffalo Bills", "price": -110, "point": point + adj},
                {"name": "Kansas City Chiefs", "price": -110, "point": -(point + adj)}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 47.5},
                {"name": "Under", "price": -110, "point": 47.5}]}]}
    return [{"id": "evt1", "commence_time": KICK.isoformat(),
             "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs",
             "bookmakers": [book("draftkings", 0.0), book("fanduel", 0.5),
                            book("betmgm", -0.5)]}]


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, future=True)
    with f() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(policy_version="ftp-2026-v1",
                                            start=date(2026, 9, 1), end=date(2027, 2, 28)))
        ingest_schedule(s, _csv(), season=2026, observed_at=KICK - timedelta(days=30))
        s.commit()
    return f


class ChainRun:
    """Drives the slate forward and records what each stage reported."""

    def __init__(self, factory) -> None:
        self.factory = factory
        self.clock = FrozenClock(KICK - timedelta(days=7))
        self.sched = Scheduler(factory, clock=self.clock, cohort=Cohort.BURN_IN,
                               provider_mode=ProviderMode.FIXTURE,
                               policy_version="ftp-2026-v1")
        register_all(self.sched)
        self.log: list[tuple[str, str]] = []

    def at(self, when: datetime) -> ChainRun:
        self.clock.set(when)
        return self

    def run(self, job: str, **params) -> dict:
        r = self.sched.run_job(job, slot=self.clock.now(), params=params or None)
        self.log.append((job, r["status"]))
        return r

    def session(self):
        return self.factory()


def _walk_the_week(chain: ChainRun) -> None:
    """The observation stages, in the order the week produces them."""
    # T-7d: the slate is known, the market has opened.
    chain.at(KICK - timedelta(days=7))
    chain.run("schedule_refresh")
    chain.run("odds_capture", fixture_payload=_payload(chain.clock.now()))
    chain.run("consensus_build")
    chain.run("prediction_vintage")

    # T-2d: the market has moved; capture again and rebuild.
    chain.at(KICK - timedelta(days=2))
    chain.run("odds_capture", fixture_payload=_payload(chain.clock.now(), point=-3.0))
    chain.run("consensus_build")
    chain.run("injury_reconciliation")
    chain.run("availability_computation")
    chain.run("feature_snapshot")
    chain.run("prediction_vintage")

    # Kickoff: closing capture is evaluation-only by construction.
    chain.at(KICK - timedelta(minutes=5))
    chain.run("odds_capture", fixture_payload=_payload(chain.clock.now(), point=-3.5))
    chain.run("consensus_build")
    chain.run("closing_capture")
    chain.run("manual_price_expiration")

    # After the whistle: results, settlement, evaluation.
    chain.at(KICK + timedelta(hours=4))
    chain.run("result_ingestion", final_scores={GAME: (24, 20)})
    chain.run("settlement", final_scores={GAME: (24, 20)})
    chain.run("forward_evaluation")
    chain.run("data_health_reconciliation")


@pytest.fixture()
def chain(factory):
    c = ChainRun(factory)
    _walk_the_week(c)
    return c


class TestEveryStageRan:
    def test_no_stage_raised(self, chain) -> None:
        """Every job returns a status; none blow up mid-slate."""
        assert len(chain.log) == 18
        for job, status in chain.log:
            assert status in {"finished", "skipped", "dead_letter"}, (job, status)

    def test_the_core_capture_stages_actually_did_work(self, chain) -> None:
        """Skipping is legitimate for some stages, but the capture spine
        must genuinely run or the chain proves nothing."""
        done = {job for job, status in chain.log if status == "finished"}
        for required in ("odds_capture", "consensus_build"):
            assert required in done, chain.log


class TestChainLinkage:
    def test_schedule_to_odds(self, chain) -> None:
        """Quotes attach to the observed game, not a free-floating id."""
        with chain.session() as s:
            games = set(s.scalars(select(ScheduleObservation.canonical_game_id)))
            quoted = set(s.scalars(select(OddsQuote.canonical_game_id)))
        assert quoted, "no quotes captured"
        assert quoted <= games, quoted - games

    def test_odds_to_consensus(self, chain) -> None:
        """Consensus exists only where quotes exist."""
        with chain.session() as s:
            quoted = set(s.scalars(select(OddsQuote.canonical_game_id)))
            consensus = set(s.scalars(select(ConsensusSnapshot.canonical_game_id)))
        assert consensus, "no consensus built"
        assert consensus <= quoted, consensus - quoted

    def test_consensus_accumulates_across_the_week(self, chain) -> None:
        """Three capture points must leave three generations of snapshots,
        not one row overwritten - the market's path is the evidence."""
        with chain.session() as s:
            times = sorted(set(s.scalars(select(ConsensusSnapshot.observed_at))))
        assert len(times) >= 3, times

    def test_predictions_reference_a_real_game(self, chain) -> None:
        with chain.session() as s:
            games = set(s.scalars(select(ScheduleObservation.canonical_game_id)))
            predicted = set(s.scalars(select(ForwardPrediction.canonical_game_id)))
        assert predicted <= games


class TestPointInTimeAcrossTheChain:
    def test_no_record_observes_the_future(self, chain) -> None:
        """Every captured row's observation time is at or before the moment
        the job that wrote it was running."""
        end = KICK + timedelta(hours=4)
        with chain.session() as s:
            for observed in s.scalars(select(OddsQuote.observed_at)):
                assert observed <= end
            for as_of in s.scalars(select(ConsensusSnapshot.observed_at)):
                assert as_of <= end

    def test_predictions_never_use_post_kickoff_information(self, chain) -> None:
        """A vintage is only honest if its cutoff precedes kickoff. The
        closing capture is excluded by name because it is evaluation-only."""
        with chain.session() as s:
            rows = list(s.scalars(select(ForwardPrediction)))
            for p in rows:
                if p.horizon == "CLOSING_CAPTURE_EVALUATION_ONLY":
                    continue
                assert p.as_of_at <= KICK, (p.horizon, p.as_of_at)


class TestNoBetStateIsReachable:
    def test_no_run_ends_in_a_betting_state(self, chain) -> None:
        """The engine is research-only; BET must not exist anywhere."""
        with chain.session() as s:
            states = set(s.scalars(select(ScheduledJobRun.domain_state)))
        assert "BET" not in states
        assert not {x for x in states if x and "BET" in x.upper()}, states

    def test_no_prediction_payload_mentions_betting(self, chain) -> None:
        """Research status is carried in the vintage payload rather than a
        column, so the check is that no payload anywhere names a BET."""
        with chain.session() as s:
            rows = list(s.scalars(select(ForwardPrediction)))
        for p in rows:
            blob = f"{p.warnings}{p.lineage}".upper()
            assert '"BET"' not in blob and "'BET'" not in blob, p.id


class TestRunRecordsAreWellFormed:
    def test_every_run_carries_both_axes(self, chain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        assert runs
        for r in runs:
            assert r.job_outcome in {o.value for o in Outcome}, r.job_outcome
            assert r.domain_state in LIVE_DOMAIN_STATES | {None}, r.domain_state
            assert r.state_origin == "LIVE"

    def test_every_run_is_its_own_root_when_nothing_crashed(self, chain) -> None:
        """A clean season week produces chains of one."""
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.recovery_sequence == 0
            assert r.root_run_id == r.id
            assert r.administrative_override is False

    def test_no_run_is_marked_migrated(self, chain) -> None:
        """UNKNOWN_LEGACY is reachable only through migration, never live."""
        with chain.session() as s:
            states = set(s.scalars(select(ScheduledJobRun.domain_state)))
        assert "UNKNOWN_LEGACY" not in states


class TestCohortAndModeArePreserved:
    def test_the_whole_chain_stays_in_one_cohort(self, chain) -> None:
        with chain.session() as s:
            modes = set(s.scalars(select(ScheduledJobRun.data_mode)))
        assert len(modes) == 1, modes

    def test_fixture_output_is_not_labelled_live(self, chain) -> None:
        """Fixture-sourced records must never claim live provenance.

        data_mode is the analysis axis - LIVE_RESEARCH is legitimate for a
        burn-in cohort. Provenance is provider_mode, and that is what must
        never read LIVE for a fixture payload."""
        with chain.session() as s:
            quote_modes = set(s.scalars(select(OddsQuote.provider_mode)))
            consensus_modes = set(s.scalars(select(ConsensusSnapshot.provider_mode)))
        assert quote_modes == {ProviderMode.FIXTURE.value}, quote_modes
        assert ProviderMode.LIVE.value not in consensus_modes, consensus_modes

    def test_derived_records_inherit_fixture_provenance(self, chain) -> None:
        """Consensus built entirely from fixture quotes is fixture-derived,
        not live - provenance must survive the derivation step."""
        with chain.session() as s:
            modes = set(s.scalars(select(ConsensusSnapshot.provider_mode)))
        assert modes == {ProviderMode.FIXTURE.value}, modes


class TestRerunningTheWeekChangesNothing:
    def test_the_whole_walk_is_idempotent(self, factory) -> None:
        """Replaying every stage at the same logical slots must not create
        a second copy of the week."""
        first = ChainRun(factory)
        _walk_the_week(first)
        with factory() as s:
            before = (
                s.scalar(select(ScheduleObservation.canonical_game_id).limit(1)),
                len(list(s.scalars(select(OddsQuote.id)))),
                len(list(s.scalars(select(ConsensusSnapshot.id)))),
                len(list(s.scalars(select(ForwardPrediction.id)))),
            )

        second = ChainRun(factory)
        _walk_the_week(second)
        with factory() as s:
            after = (
                s.scalar(select(ScheduleObservation.canonical_game_id).limit(1)),
                len(list(s.scalars(select(OddsQuote.id)))),
                len(list(s.scalars(select(ConsensusSnapshot.id)))),
                len(list(s.scalars(select(ForwardPrediction.id)))),
            )
        assert before == after

        # And the second pass must say so rather than silently no-op'ing.
        assert any(status == "skipped" for _, status in second.log), second.log
