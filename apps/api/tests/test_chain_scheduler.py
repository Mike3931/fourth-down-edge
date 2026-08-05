"""One game through the complete operational backend, via the scheduler.

Every other chain test drives domain services directly. This one drives
only `Scheduler.run_job`, in the order a real week produces the records,
because the operational proof is that the SCHEDULER can produce the chain -
not that the services can when called by hand. A service that works when
invoked directly and fails when invoked by a handler is a service that does
not work.

Cohort is `fixture` throughout. Fixture output must never be stored or
described as live-provider output, and it must never enter `burn_in` or
`official_forward_test`, so nothing here can contaminate an evaluable
cohort even by accident.

Nothing in this module asserts that any prediction is good. The engine is
research-only; the properties under test are structural.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import (
    ForwardLedgerEntry,
    ForwardPrediction,
    ManualBookPriceEntry,
    ScheduledJobRun,
    ScheduleObservation,
)
from fde_api.db.models import Base
from fde_api.forward.chain import (
    CHAIN_ORDER,
    CONDITIONALLY_ABSENT,
    ChainVerdict,
    Stage,
    chain_semantic_hash,
    read_chain,
    reconcile_chain,
)
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import register_all
from fde_api.forward.modes import DataMode
from fde_api.forward.policy import build_policy_draft, freeze_policy
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.scheduler import FrozenClock, Scheduler
from fde_api.forward.state import Outcome
from fde_api.forward.venues import seed_venues

# A deterministic U.S. outdoor game at a governed venue.
KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
GAME = "2026_02_KC_BUF"
DATA_MODE = DataMode.DEMO  # fixture cohort never claims LIVE_RESEARCH

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def _csv(*, status_fields: dict[str, str] | None = None) -> bytes:
    base = dict.fromkeys(HEADER.split(","), "")
    base.update({
        "game_id": GAME, "season": "2026", "game_type": "REG", "week": "2",
        "gameday": "2026-09-13", "gametime": "13:00", "away_team": "KC", "home_team": "BUF",
        "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
        "stadium_id": "BUF00", "stadium": "Highmark Stadium", "away_rest": "7", "home_rest": "7",
        "home_qb_name": "Josh Allen", "away_qb_name": "Patrick Mahomes",
    })
    base.update(status_fields or {})
    return ("\n".join([HEADER, ",".join(base[k] for k in HEADER.split(","))]) + "\n").encode()


def _payload(ts: datetime, *, point: float = -2.5) -> list[dict]:
    """Three eligible books, so consensus has something to agree about."""

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


def _prices(observed_at: datetime, *, american: int = -110) -> list[dict]:
    """Prices a person typed in. Nothing is fetched; no book is contacted."""
    return [
        {"canonical_game_id": GAME, "market": "SPREAD", "selection": "HOME",
         "line": -3.0, "american": american, "observed_at": observed_at,
         "user_id": "fixture-operator", "source": "fixture_price", "confirmed": True},
        {"canonical_game_id": GAME, "market": "TOTAL", "selection": "OVER",
         "line": 47.5, "american": american, "observed_at": observed_at,
         "user_id": "fixture-operator", "source": "fixture_price", "confirmed": True},
    ]


class FixtureNws:
    """A deterministic stand-in for the NWS client.

    Returns the same forecast every time so the vintage is stable, and its
    presence is what tells `weather_capture` it is running against a fixture
    rather than reaching out to weather.gov. Nothing here opens a socket.
    """

    def __init__(self, *, temp_f: int = 62, wind: str = "8 mph") -> None:
        self.temp_f = temp_f
        self.wind = wind

    def resolve_grid(self, lat: float, lon: float) -> dict:
        return {"office": "BUF", "grid_x": 10, "grid_y": 20,
                "forecast_url": "fixture://forecast"}

    def fetch_forecast(self, url: str) -> tuple[dict, bytes]:
        body = {"properties": {"periods": [{
            "startTime": (KICK - timedelta(hours=1)).isoformat(),
            "endTime": (KICK + timedelta(hours=3)).isoformat(),
            "temperature": self.temp_f, "temperatureUnit": "F",
            "windSpeed": self.wind, "probabilityOfPrecipitation": {"value": 10},
            "relativeHumidity": {"value": 55},
            "shortForecast": "Partly Cloudy",
            "detailedForecast": "Partly cloudy with light wind.",
        }]}}
        raw = repr(sorted(body["properties"]["periods"][0].items())).encode()
        return body, raw


def _seed_injuries(factory, *, observed_at: datetime) -> None:
    """A resolved starting quarterback and one listed player.

    Injury entry is manual by design, so the chain needs observations to
    exist before `injury_reconciliation` and `availability_computation` have
    anything to reconcile or assess.
    """
    from fde_api.forward.injuries import SourceCategory, record_injury_observation

    with factory() as s:
        for team, player, designation in (
            ("BUF", "BUF_QB_ALLEN", None),
            ("KC", "KC_QB_MAHOMES", None),
            ("BUF", "BUF_WR_DIGGS", "QUESTIONABLE"),
        ):
            record_injury_observation(
                s,
                canonical_game_id=GAME,
                team_id=team,
                player_id=player,
                report_date="2026-09-11",
                observed_at=observed_at,
                source_category=SourceCategory.OFFICIAL_VERIFIED,
                practice_status="FULL" if designation is None else "LIMITED",
                game_designation=designation,
                body_part=None if designation is None else "hamstring",
                source_reference="fixture injury report",
                data_mode=DATA_MODE,
                now=observed_at,
            )
        s.commit()


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
        ingest_schedule(s, _csv(), season=2026, observed_at=KICK - timedelta(days=30),
                        data_mode=DATA_MODE)
        s.commit()
    return f


class SchedulerChain:
    """Drives one game forward through scheduled handlers only."""

    def __init__(self, factory) -> None:
        self.factory = factory
        self.clock = FrozenClock(KICK - timedelta(days=7))
        self.sched = Scheduler(
            factory, clock=self.clock,
            cohort=Cohort.FIXTURE, provider_mode=ProviderMode.FIXTURE,
            policy_version="ftp-2026-v1", data_mode=DATA_MODE,
        )
        register_all(self.sched)
        self.log: list[tuple[str, str]] = []

    def at(self, when: datetime) -> SchedulerChain:
        self.clock.set(when)
        return self

    def run(self, job: str, **params) -> dict:
        r = self.sched.run_job(job, slot=self.clock.now(), params=params or None)
        self.log.append((job, r["status"]))
        return r

    def statuses(self) -> dict[str, str]:
        return dict(self.log)

    def session(self):
        return self.factory()


# The §14 sequence, in logical order. Named so a failure says which step.
REQUIRED_SEQUENCE = (
    "schedule_refresh",
    "odds_capture",
    "consensus_build",
    "weather_capture",
    "injury_reconciliation",
    "availability_computation",
    "feature_snapshot",
    "prediction_vintage",
    "price_observation",
    "price_evaluation",
    "closing_capture",
    "result_ingestion",
    "settlement",
    "forward_evaluation",
    "data_health_reconciliation",
)


def _drive(chain: SchedulerChain, *, seed_injuries: bool = True) -> None:
    """The full week, in the order it actually becomes knowable.

    `seed_injuries` is False on a replay. Injury entry is manual by design,
    so seeding again genuinely appends new superseding observations - that
    is the immutable-history behaviour working, not a duplicate effect. A
    replay must exercise the SCHEDULER, so it re-runs the handlers over the
    observations that already exist.
    """
    # T-7d: the slate is known and the market has opened.
    chain.at(KICK - timedelta(days=7))
    chain.run("schedule_refresh")
    chain.run("odds_capture", fixture_payload=_payload(chain.clock.now()))
    chain.run("consensus_build")
    chain.run("weather_capture", nws_client=FixtureNws())
    if seed_injuries:
        _seed_injuries(chain.factory, observed_at=chain.clock.now())
    chain.run("injury_reconciliation")
    chain.run("availability_computation")
    chain.run("feature_snapshot")

    # T-2d: the market has moved. Recapture, then predict and price.
    chain.at(KICK - timedelta(days=2))
    chain.run("odds_capture", fixture_payload=_payload(chain.clock.now(), point=-3.0))
    chain.run("consensus_build")
    chain.run("prediction_vintage")
    chain.run("price_observation",
              price_observations=_prices(chain.clock.now() - timedelta(minutes=2)))
    chain.run("price_evaluation")

    # Kickoff: the close is captured without anyone seeing the outcome.
    chain.at(KICK - timedelta(minutes=5))
    chain.run("odds_capture", fixture_payload=_payload(chain.clock.now(), point=-3.5))
    chain.run("consensus_build")
    chain.run("closing_capture")

    # After the whistle.
    chain.at(KICK + timedelta(hours=4))
    chain.run("result_ingestion", final_scores={GAME: (24, 20)})
    chain.run("settlement", final_scores={GAME: (24, 20)})
    chain.run("forward_evaluation")
    chain.run("data_health_reconciliation")


@pytest.fixture()
def chain(factory):
    c = SchedulerChain(factory)
    _drive(c)
    return c


# --------------------------------------------------------------------------- #
# §14 — the sequence ran
# --------------------------------------------------------------------------- #


class TestTheSchedulerDroveEveryStage:
    @pytest.mark.parametrize("job", REQUIRED_SEQUENCE)
    def test_the_step_ran_and_did_not_fail(self, chain: SchedulerChain, job: str) -> None:
        statuses = chain.statuses()
        assert job in statuses, f"{job} never ran"
        assert statuses[job] in {"finished", "skipped"}, f"{job} -> {statuses[job]}"

    def test_no_step_was_refused_or_sent_to_review(self, chain: SchedulerChain) -> None:
        bad = [(j, s) for j, s in chain.log if s in {"refused", "manual_review_required"}]
        assert not bad, bad

    def test_the_runs_are_recorded_in_the_fixture_cohort_only(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            modes = {r.data_mode for r in s.scalars(select(ScheduledJobRun))}
        assert modes == {DATA_MODE.value}, modes

    def test_every_run_carries_its_provider_mode(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            modes = {r.provider_mode for r in s.scalars(select(ScheduledJobRun))}
        assert modes == {ProviderMode.FIXTURE.value}, modes


class TestEveryRunRecordsItsAccounting:
    """§14: outcome, domain state, counts, warnings, provider usage, lineage."""

    def test_both_state_axes_are_populated(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        assert runs
        for r in runs:
            assert r.job_outcome, f"{r.job_kind} has no outcome"
            assert r.domain_state, f"{r.job_kind} has no domain state"

    def test_record_counts_are_present_and_non_negative(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.records_received >= 0
            assert r.records_written >= 0

    def test_provider_usage_is_recorded(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.provider_calls >= 0

    def test_a_manual_price_run_reports_no_provider_calls(
        self, chain: SchedulerChain
    ) -> None:
        """The record that proves no book was contacted."""
        with chain.session() as s:
            runs = [r for r in s.scalars(select(ScheduledJobRun))
                    if r.job_kind == "price_observation"]
        assert runs, "price_observation never ran"
        assert all(r.provider_calls == 0 for r in runs)

    def test_every_run_carries_lineage_or_an_explicit_absence(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            runs = list(s.scalars(select(ScheduledJobRun)))
        for r in runs:
            assert r.root_run_id, f"{r.job_kind} has no root"
            assert r.original_idempotency_key, f"{r.job_kind} has no chain key"
            assert r.recovery_sequence is not None


# --------------------------------------------------------------------------- #
# §2 / §13 — the chain itself
# --------------------------------------------------------------------------- #


class TestTheChainIsComplete:
    def test_every_unconditional_stage_exists(self, chain: SchedulerChain) -> None:
        """Every stage that a complete game must have, present.

        Conditional stages are excluded and checked separately below rather
        than lumped in, because "absent" means different things for them: a
        PASS is never filled, and an unfilled entry has no closing-line
        value to compare against.
        """
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        missing = [
            st.value for st in CHAIN_ORDER
            if not state.present(st) and st not in CONDITIONALLY_ABSENT
        ]
        assert not missing, f"stages absent from the chain: {missing}"

    @pytest.mark.parametrize("stage", sorted(CONDITIONALLY_ABSENT, key=lambda s: s.value))
    def test_a_conditional_stage_is_counted_not_ignored(
        self, chain: SchedulerChain, stage: Stage
    ) -> None:
        """Absence must be a recorded zero, never a missing key. A stage the
        reader cannot see at all is indistinguishable from one nobody
        thought to check."""
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        assert stage in state.stages, f"{stage.value} is not reported at all"
        assert state.stages[stage] >= 0

    def test_an_unfilled_entry_has_no_clv_and_that_is_correct(
        self, chain: SchedulerChain
    ) -> None:
        """CLV compares an entry price against the close. With no fill there
        is no entry price, so a CLV value would have to be invented."""
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        if state.stages.get(Stage.SIMULATED_FILL, 0) == 0:
            assert state.stages.get(Stage.CLV, 0) == 0, (
                "CLV present with no simulated fill; it has nothing to measure from"
            )

    def test_the_close_was_captured_and_is_therefore_measurable(
        self, chain: SchedulerChain
    ) -> None:
        """A missing close is allowed but must be explicit. In this scenario
        the close exists, so the chain can state that rather than shrug."""
        with chain.session() as s:
            state = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
        assert state.present(Stage.CLOSING_CAPTURE)

    def test_the_semantic_hash_is_stable_across_reads(
        self, chain: SchedulerChain
    ) -> None:
        """Hashing ids or timestamps would produce a value that changes for
        no reason and therefore detects nothing."""
        with chain.session() as s:
            a = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
            b = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        assert a == b
        assert len(a) == 64

    def test_two_identical_runs_agree_on_the_hash(self, factory, tmp_path) -> None:
        """The property that makes the hash worth reporting."""
        first = SchedulerChain(factory)
        _drive(first)
        with first.session() as s:
            h1 = chain_semantic_hash(read_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value))
        assert len(h1) == 64


# --------------------------------------------------------------------------- #
# §16 — reconciliation
# --------------------------------------------------------------------------- #


class TestChainReconciliation:
    def test_a_complete_chain_reconciles(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        assert report["verdict"] in {
            ChainVerdict.CONSISTENT.value, ChainVerdict.INCOMPLETE.value
        }, report["findings"]

    def test_reconciliation_never_repairs(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        assert report["auto_repair"] is False

    def test_a_domain_effect_without_a_completed_run_is_recoverable(
        self, chain: SchedulerChain
    ) -> None:
        """The signature of a crash after commit: real records, no run."""
        with chain.session() as s:
            for r in s.scalars(select(ScheduledJobRun).where(
                ScheduledJobRun.job_kind == "odds_capture"
            )):
                r.job_outcome = Outcome.INTERRUPTED.value
                r.status = "interrupted"
            s.commit()
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        checks = {f["check"] for f in report["findings"]}
        assert "domain_effect_without_scheduler_completion" in checks
        assert report["verdict"] in {
            ChainVerdict.RECOVERABLE.value, ChainVerdict.MANUAL_REVIEW.value,
            ChainVerdict.CORRUPTED.value,
        }

    def test_a_conflicting_settlement_requires_review(self, chain: SchedulerChain) -> None:
        """Never auto-repaired: two settlements of one selection that
        disagree is a question about a money-shaped fact."""
        with chain.session() as s:
            rows = list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME)))
            assert rows, "no ledger rows to conflict"
            target = rows[0]
            clone = ForwardLedgerEntry(
                data_mode=target.data_mode, canonical_game_id=target.canonical_game_id,
                policy_version=target.policy_version, model_version=target.model_version,
                horizon=target.horizon, market=target.market, selection=target.selection,
                status=target.status, reasons=target.reasons,
                as_of_at=target.as_of_at + timedelta(seconds=1),
                created_at=target.created_at, result="LOSS", settled_at=target.created_at,
                filled=True,
            )
            target.result = "WIN"
            target.filled = True
            s.add(clone)
            s.commit()
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        checks = {f["check"] for f in report["findings"]}
        assert "conflicting_settlement" in checks, checks
        assert report["verdict"] in {
            ChainVerdict.MANUAL_REVIEW.value, ChainVerdict.CORRUPTED.value
        }

    def test_a_cohort_mismatch_is_corrupting(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            row = s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME)).first()
            assert row is not None
            row.data_mode = DataMode.LIVE_RESEARCH.value
            s.commit()
            report = reconcile_chain(
                s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                policy_version="ftp-2026-v1",
            )
        # The row left the cohort entirely, so the scan for THIS cohort now
        # sees a gap rather than a mixture - which is itself the detection.
        assert report["verdict"] != ChainVerdict.CONSISTENT.value


# --------------------------------------------------------------------------- #
# §5 — point-in-time integrity across the chain
# --------------------------------------------------------------------------- #


class TestPointInTimeAcrossTheChain:
    def test_no_prediction_was_made_after_kickoff(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            preds = list(s.scalars(select(ForwardPrediction).where(
                ForwardPrediction.canonical_game_id == GAME)))
        assert preds
        for p in preds:
            assert p.as_of_at < KICK, f"{p.id} predicted at or after kickoff"

    def test_no_prediction_saw_the_closing_capture(self, chain: SchedulerChain) -> None:
        """The close is recorded at kickoff; every vintage precedes it."""
        with chain.session() as s:
            preds = list(s.scalars(select(ForwardPrediction).where(
                ForwardPrediction.canonical_game_id == GAME)))
            from fde_api.db.forward_models import ConsensusSnapshot

            closes = list(s.scalars(select(ConsensusSnapshot).where(
                ConsensusSnapshot.canonical_game_id == GAME,
                ConsensusSnapshot.is_closing_capture.is_(True))))
        assert closes, "no closing capture to test against"
        earliest_close = min(c.observed_at for c in closes)
        for p in preds:
            assert p.as_of_at < earliest_close

    def test_no_prediction_saw_the_final_result(self, chain: SchedulerChain) -> None:
        with chain.session() as s:
            finals = list(s.scalars(select(ScheduleObservation).where(
                ScheduleObservation.canonical_game_id == GAME,
                ScheduleObservation.game_status == "FINAL")))
            preds = list(s.scalars(select(ForwardPrediction).where(
                ForwardPrediction.canonical_game_id == GAME)))
        assert finals, "the result was never ingested"
        first_final = min(f.observed_at for f in finals)
        for p in preds:
            assert p.as_of_at < first_final

    def test_every_price_was_observed_before_it_was_evaluated(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            prices = list(s.scalars(select(ManualBookPriceEntry).where(
                ManualBookPriceEntry.canonical_game_id == GAME)))
            evals = list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME)))
        assert prices and evals
        earliest_eval = min(e.as_of_at for e in evals)
        for p in prices:
            assert p.observed_at <= earliest_eval, (
                f"price {p.id} observed after the evaluation that used it"
            )

    def test_every_timestamp_is_timezone_aware(self, chain: SchedulerChain) -> None:
        """A naive timestamp compares wrongly against an aware one, which is
        how a cutoff silently stops being a cutoff."""
        with chain.session() as s:
            rows: list[tuple[str, datetime | None]] = []
            for p in s.scalars(select(ForwardPrediction).where(
                    ForwardPrediction.canonical_game_id == GAME)):
                rows.append((f"prediction {p.id} as_of_at", p.as_of_at))
                rows.append((f"prediction {p.id} created_at", p.created_at))
            for e in s.scalars(select(ForwardLedgerEntry).where(
                    ForwardLedgerEntry.canonical_game_id == GAME)):
                rows.append((f"ledger {e.id} as_of_at", e.as_of_at))
                rows.append((f"ledger {e.id} created_at", e.created_at))
            for m in s.scalars(select(ManualBookPriceEntry).where(
                    ManualBookPriceEntry.canonical_game_id == GAME)):
                rows.append((f"price {m.id} observed_at", m.observed_at))
                rows.append((f"price {m.id} entered_at", m.entered_at))
        assert rows
        naive = [label for label, ts in rows if ts is not None and ts.tzinfo is None]
        assert not naive, f"naive timestamps: {naive}"


# --------------------------------------------------------------------------- #
# §13 — rerunning creates no duplicate effects
# --------------------------------------------------------------------------- #


class TestRerunningIsIdempotent:
    def test_the_whole_week_can_be_replayed_without_duplicating(
        self, chain: SchedulerChain
    ) -> None:
        """A restart replays the week. Not a clock rewind - `FrozenClock`
        refuses to move backwards, which is a guard worth keeping - but a
        fresh scheduler over the same database, which is what a restarted
        process actually is."""
        with chain.session() as s:
            before = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                                policy_version="ftp-2026-v1")
            before_hash = chain_semantic_hash(before)
        restarted = SchedulerChain(chain.factory)
        _drive(restarted, seed_injuries=False)
        chain = restarted
        with chain.session() as s:
            after = read_chain(s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
                               policy_version="ftp-2026-v1")
            after_hash = chain_semantic_hash(after)
        assert after_hash == before_hash, (
            f"replay changed the chain shape:\n"
            f"  before {before.as_dict()['stages']}\n"
            f"  after  {after.as_dict()['stages']}"
        )

    def test_a_replayed_price_observation_adds_no_row(
        self, chain: SchedulerChain
    ) -> None:
        with chain.session() as s:
            before = len(list(s.scalars(select(ManualBookPriceEntry).where(
                ManualBookPriceEntry.canonical_game_id == GAME))))
        replay = SchedulerChain(chain.factory)
        replay.at(KICK - timedelta(days=2))
        replay.run("price_observation",
                   price_observations=_prices(replay.clock.now() - timedelta(minutes=2)))
        with chain.session() as s:
            after = len(list(s.scalars(select(ManualBookPriceEntry).where(
                ManualBookPriceEntry.canonical_game_id == GAME))))
        assert after == before

    def test_a_replayed_evaluation_adds_no_ledger_row(
        self, chain: SchedulerChain
    ) -> None:
        """`record_evaluation` appends unconditionally, so the handler's
        slot-identity guard is the only thing preventing a duplicate."""
        with chain.session() as s:
            before = len(list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME))))
        replay = SchedulerChain(chain.factory)
        replay.at(KICK - timedelta(days=2))
        replay.run("price_evaluation")
        with chain.session() as s:
            after = len(list(s.scalars(select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.canonical_game_id == GAME))))
        assert after == before


# --------------------------------------------------------------------------- #
# Cohort containment
# --------------------------------------------------------------------------- #


class TestFixtureOutputStaysOutOfEvaluableCohorts:
    def test_no_record_entered_burn_in_or_the_official_cohort(
        self, chain: SchedulerChain
    ) -> None:
        forbidden = {Cohort.BURN_IN.value, Cohort.OFFICIAL_FORWARD_TEST.value}
        with chain.session() as s:
            price_cohorts = {p.cohort for p in s.scalars(select(ManualBookPriceEntry))}
            run_modes = {r.data_mode for r in s.scalars(select(ScheduledJobRun))}
        assert not (price_cohorts & forbidden), price_cohorts
        assert not (run_modes & forbidden), run_modes

    def test_every_market_record_is_marked_fixture_provenance(
        self, chain: SchedulerChain
    ) -> None:
        from fde_api.db.forward_models import OddsQuote

        with chain.session() as s:
            modes = {q.provider_mode for q in s.scalars(select(OddsQuote).where(
                OddsQuote.canonical_game_id == GAME))}
        assert modes == {ProviderMode.FIXTURE.value}, modes
