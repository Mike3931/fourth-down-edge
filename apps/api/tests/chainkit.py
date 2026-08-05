"""Shared harness for the record-chain tests, driven by the manifest.

Deliberately NOT a `test_*` module: it holds the scheduler driver the chain
tests share. The SCENARIO lives in `scenario_manifest.py` and is read from
there — this file decides only how the scheduler executes it.

That separation is the point. Two hand-maintained fixtures drifted apart
and made the parity gate fail for a reason that had nothing to do with the
system under test. Now a change to what happens in the world is a change to
the manifest, visible as a changed content hash, and both paths see it.

The pytest fixtures live in `conftest.py`, where pytest finds them by name
without any import.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import register_all
from fde_api.forward.modes import DataMode
from fde_api.forward.scheduler import FrozenClock, Scheduler
from scenario_manifest import COMPLETE_GAME, FixtureNws, ScenarioManifest, schedule_csv

# One import site for the scenario, so nothing re-declares it.
MANIFEST: ScenarioManifest = COMPLETE_GAME
KICK = MANIFEST.kickoff_utc
GAME = MANIFEST.canonical_game_id
DATA_MODE = DataMode(MANIFEST.data_mode)
COHORT = Cohort(MANIFEST.cohort)
POLICY = MANIFEST.policy_version


def csv_bytes() -> bytes:
    return schedule_csv(MANIFEST)


def payload(at: datetime, *, point: float | None = None) -> list[dict]:
    """The provider payload for the manifest slot at `at`.

    A slot the manifest does not declare raises rather than being invented:
    an unlisted payload is a fixture only one path would ever see, which is
    exactly the drift this module exists to prevent.
    """
    for slot in MANIFEST.odds_slots:
        if slot.at == at and (point is None or slot.spread_point == point):
            return MANIFEST.payload_for(slot)
    raise KeyError(
        f"no odds slot at {at.isoformat()} (point={point}) in {MANIFEST.version}; "
        "add it to the manifest rather than hand-building a payload"
    )


def payload_for_slot(slot: datetime) -> list[dict]:
    """The payload belonging to a slot, stable across replays."""
    return payload(slot)


def prices(observed_at: datetime | None = None) -> list[dict]:
    """The manifest's price observations, in handler-parameter shape.

    `observed_at` is accepted and ignored: the manifest states when each
    price was SEEN, and letting a caller override that is how the two paths
    came to record different observation times for the same price.
    """
    return [
        {
            "canonical_game_id": MANIFEST.canonical_game_id,
            "market": p.market,
            "selection": p.selection,
            "line": p.line,
            "american": p.american,
            "observed_at": p.observed_at,
            "user_id": p.user_id,
            "source": p.source,
            "confirmed": p.confirmed,
        }
        for p in MANIFEST.price_observations
    ]


def seed_injuries(factory, *, observed_at: datetime | None = None) -> None:
    """The manifest's injury vintages.

    Injury entry is manual by design, so the chain needs observations to
    exist before reconciliation and assessment have anything to work on.
    """
    from fde_api.forward.injuries import SourceCategory, record_injury_observation

    with factory() as s:
        for v in MANIFEST.injury_vintages:
            record_injury_observation(
                s,
                canonical_game_id=MANIFEST.canonical_game_id,
                team_id=v.team_id,
                player_id=v.player_id,
                report_date=v.report_date,
                observed_at=v.at,
                source_category=SourceCategory(v.source_category),
                practice_status=v.practice_status,
                game_designation=v.game_designation,
                body_part=v.body_part,
                source_reference="fixture injury report",
                data_mode=DATA_MODE,
                now=v.at,
            )
        s.commit()


def seed_venues_and_policy(factory) -> None:
    """Everything the scenario needs before either path can run."""
    from fde_api.forward.policy import build_policy_draft, freeze_policy
    from fde_api.forward.schedule import ingest_schedule
    from fde_api.forward.venues import seed_venues

    m = MANIFEST
    with factory() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(
            policy_version=m.policy_version,
            start=(m.kickoff_utc - timedelta(days=60)).date(),
            end=(m.kickoff_utc + timedelta(days=160)).date(),
        ))
        ingest_schedule(s, csv_bytes(), season=m.season,
                        observed_at=m.schedule_observed_at, data_mode=DATA_MODE)
        s.commit()


class SchedulerChain:
    """Drives one game forward through scheduled handlers only."""

    def __init__(self, factory) -> None:
        self.factory = factory
        self.clock = FrozenClock(MANIFEST.odds_slots[0].at)
        self.sched = Scheduler(
            factory, clock=self.clock,
            cohort=COHORT, provider_mode=ProviderMode(MANIFEST.provider_mode),
            policy_version=POLICY, data_mode=DATA_MODE,
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

    def runs(self) -> list[ScheduledJobRun]:
        with self.factory() as s:
            rows = list(s.scalars(select(ScheduledJobRun)))
            for r in rows:
                s.expunge(r)
            return rows


# The required sequence, in logical order. Named so a failure says which step.
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


def drive(chain: SchedulerChain, *, with_injuries: bool = True) -> None:
    """Execute the manifest through the scheduler.

    Every moment and every input comes from the manifest. `with_injuries`
    is False on a replay: injury entry is manual, so re-seeding appends
    genuinely new superseding observations - the immutable-history
    behaviour working, not a duplicate effect. A replay must exercise the
    SCHEDULER over the observations that already exist.
    """
    m = MANIFEST
    opening, mid, closing = m.odds_slots

    # The market opens; slate, weather and injury picture become known.
    chain.at(opening.at)
    chain.run("schedule_refresh")
    chain.run("odds_capture", fixture_payload=m.payload_for(opening))
    chain.run("consensus_build")
    chain.run("weather_capture", nws_client=FixtureNws(m.weather_vintages[0]))
    if with_injuries:
        seed_injuries(chain.factory)
    chain.run("injury_reconciliation")
    chain.run("availability_computation")
    chain.run("feature_snapshot")

    # The market has moved. Recapture, predict, price, evaluate.
    chain.at(mid.at)
    chain.run("odds_capture", fixture_payload=m.payload_for(mid))
    chain.run("consensus_build")
    # The manifest owns the model output. Without it the handler generates
    # DATA_INCOMPLETE vintages carrying no probabilities, while the direct
    # chain - which was passed the moments - produces real ones. That is
    # not a disagreement about the chain; it is one path being handed the
    # model and the other not.
    chain.run("prediction_vintage", moments=m.moments,
              home_qb=m.home_qb_id, away_qb=m.away_qb_id)
    chain.run("price_observation", price_observations=prices())
    chain.run("price_evaluation")

    # Kickoff: the close is captured without anyone seeing the outcome.
    chain.at(closing.at)
    chain.run("odds_capture", fixture_payload=m.payload_for(closing))
    chain.run("consensus_build")
    chain.run("closing_capture")

    # After the whistle.
    chain.at(m.result_observed_at)
    scores = {m.canonical_game_id: (m.home_score, m.away_score)}
    chain.run("result_ingestion", final_scores=scores)
    chain.run("settlement", final_scores=scores)
    chain.run("forward_evaluation")
    chain.run("data_health_reconciliation")
