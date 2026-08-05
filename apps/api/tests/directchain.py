"""The record chain driven by domain services, with no scheduler at all.

Deliberately NOT a `test_*` module: it is the harness the direct-chain and
parity tests share.

Why a second path exists. The scheduler-driven chain proves the operational
system works. It cannot prove the DOMAIN works, because every service is
reached through a handler that supplies its arguments - so a handler that
quietly passes the wrong cutoff, or reads a value the service should have
derived, produces a chain that looks correct end to end. Driving the same
services directly, with the same fixture inputs, gives a second opinion
from a different caller.

The two paths must agree about every domain fact. Where they disagree, one
of them is wrong, and neither passing on its own tells you which.

Nothing here imports `fde_api.forward.handlers` or constructs a
`Scheduler`. `test_chain_direct.py` proves that by parsing this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ForwardPrediction, ScheduleObservation
from fde_api.forward.closing import CLOSING_RULE_VERSION, capture_close
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
GAME = "2026_02_KC_BUF"
POLICY = "ftp-2026-v1"
MODE = DataMode.DEMO
COHORT = Cohort.FIXTURE

# The moments the frozen model would produce for this fixture. Supplied
# rather than computed so both chains see identical model output - the
# property under test is the CHAIN, not the model, and letting the model
# vary between paths would make a parity failure ambiguous.
MOMENTS = (3.2, 13.5, 47.0, 10.5)

# The price the operator typed. Generous enough that the frozen rules reach
# RESEARCH_CANDIDATE on their own; no threshold is touched anywhere.
ENTRY_AMERICAN = 150
ENTRY_LINE = -3.0


@dataclass
class DirectChainLog:
    """What each stage produced, for reporting and assertion."""

    stages: dict[str, Any] = field(default_factory=dict)

    def record(self, stage: str, value: Any) -> Any:
        self.stages[stage] = value
        return value

    def summary(self) -> dict[str, Any]:
        return {k: (len(v) if isinstance(v, list) else bool(v))
                for k, v in self.stages.items()}


def seed(factory: sessionmaker) -> None:
    """Venues, the frozen policy, and the governed slate."""
    from chainkit import csv_bytes
    from fde_api.forward.policy import build_policy_draft, freeze_policy
    from fde_api.forward.schedule import ingest_schedule
    from fde_api.forward.venues import seed_venues

    with factory() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(
            policy_version=POLICY, start=date(2026, 9, 1), end=date(2027, 2, 28)))
        ingest_schedule(s, csv_bytes(), season=2026,
                        observed_at=KICK - timedelta(days=30), data_mode=MODE)
        s.commit()


def _game(session: Session) -> ScheduleObservation:
    from fde_api.forward.schedule import current_schedule_state

    obs = current_schedule_state(session, GAME, MODE)
    assert obs is not None, "the slate was never seeded"
    return obs


def run_direct_chain(
    factory: sessionmaker,
    *,
    with_injuries: bool = True,
    entry_american: int = ENTRY_AMERICAN,
    close_line: float = -4.0,
    final_score: tuple[int, int] = (24, 20),
    skip_close: bool = False,
) -> DirectChainLog:
    """Walk one game through every domain service, in knowable order."""
    from chainkit import FixtureNws, payload
    from fde_api.forward.closing import closing_snapshot_for
    from fde_api.forward.consensus import build_consensus
    from fde_api.forward.injuries import (
        SourceCategory,
        assess_player,
        observations_as_of,
        record_injury_observation,
        resolve_starting_qb,
    )
    from fde_api.forward.ledger import (
        evaluate_candidate,
        record_evaluation,
        settle_entry,
    )
    from fde_api.forward.odds import capture_odds
    from fde_api.forward.policy import load_policy
    from fde_api.forward.prices import (
        PriceObservation,
        current_price,
        price_age_seconds,
        record_price_observation,
    )
    from fde_api.forward.results import ResultObservation, ingest_result
    from fde_api.forward.vintages import generate_vintage
    from fde_api.forward.weather import capture_forecast_for_game

    log = DirectChainLog()
    t7 = KICK - timedelta(days=7)
    t2 = KICK - timedelta(days=2)
    t_close = KICK - timedelta(minutes=5)
    t_post = KICK + timedelta(hours=4)

    with factory() as s:
        policy = load_policy(s, POLICY)
        game = _game(s)
        log.record("schedule_observation", game)

        # --- observations ------------------------------------------------ #
        for when, point in ((t7, -2.5), (t2, -3.0), (t_close, close_line)):
            capture_odds(
                s, payload(when, point=point), provider_mode=ProviderMode.FIXTURE,
                observed_at=when, data_mode=MODE, request_id=f"direct:{when.isoformat()}",
            )
        s.flush()
        log.record("odds_observations", when)

        from fde_api.db.forward_models import Venue

        venue = s.get(Venue, game.stadium_id) if game.stadium_id else None
        if venue is not None and venue.weather_applicable and venue.country == "US":
            log.record("weather_vintage", capture_forecast_for_game(
                s, canonical_game_id=GAME, venue=venue, kickoff_utc=KICK,
                client=FixtureNws(), data_mode=MODE, observed_at=t7,
            ))
        else:
            log.record("weather_vintage", None)

        if with_injuries:
            for team, player, designation in (
                ("BUF", "BUF_QB_ALLEN", None),
                ("KC", "KC_QB_MAHOMES", None),
                ("BUF", "BUF_WR_DIGGS", "QUESTIONABLE"),
            ):
                record_injury_observation(
                    s, canonical_game_id=GAME, team_id=team, player_id=player,
                    report_date="2026-09-11", observed_at=t7,
                    source_category=SourceCategory.OFFICIAL_VERIFIED,
                    practice_status="FULL" if designation is None else "LIMITED",
                    game_designation=designation,
                    body_part=None if designation is None else "hamstring",
                    source_reference="fixture injury report",
                    data_mode=MODE, now=t7,
                )
        s.flush()
        log.record("injury_observations", observations_as_of(
            s, canonical_game_id=GAME, as_of_at=t2, data_mode=MODE))

        # --- derived ------------------------------------------------------ #
        for market in policy.market_selection.markets:
            for when in (t7, t2, t_close):
                build_consensus(s, canonical_game_id=GAME, market=market,
                                as_of_at=when, kickoff_utc=KICK, data_mode=MODE)
        s.flush()
        log.record("consensus_snapshot", True)

        assessments = []
        for team, player in (("BUF", "BUF_QB_ALLEN"), ("KC", "KC_QB_MAHOMES"),
                             ("BUF", "BUF_WR_DIGGS")):
            a = assess_player(
                s, canonical_game_id=GAME, team_id=team, player_id=player,
                as_of_at=t2, data_mode=MODE,
            )
            if a is not None:
                assessments.append(a)
        s.flush()
        log.record("availability_assessment", assessments)
        log.record("feature_snapshot", resolve_starting_qb(
            s, canonical_game_id=GAME, team_id="BUF", expected_qb_id="BUF_QB_ALLEN",
            as_of_at=t2, data_mode=MODE))

        # --- prediction --------------------------------------------------- #
        vintages = []
        for horizon in ("PRACTICE_UPDATE",):
            pred, _inputs = generate_vintage(
                s, game=game, horizon=horizon, policy=policy, moments=MOMENTS,
                data_mode=MODE, now=t2, expected_home_qb="BUF_QB_ALLEN",
                expected_away_qb="KC_QB_MAHOMES",
            )
            if pred is not None:
                vintages.append(pred)
        s.flush()
        log.record("prediction_vintage", vintages)

        # --- price and evaluation ----------------------------------------- #
        record_price_observation(
            s,
            PriceObservation(
                canonical_game_id=GAME, market="SPREAD", selection="HOME",
                line=ENTRY_LINE, american=entry_american,
                observed_at=t2 - timedelta(minutes=2), user_id="fixture-operator",
                source="fixture_price", cohort=COHORT,
                provider_mode=ProviderMode.FIXTURE, policy_version=POLICY,
                data_mode=MODE, confirmed=True,
            ),
            now=t2,
        )
        s.flush()
        price = current_price(s, canonical_game_id=GAME, market="SPREAD",
                              selection="HOME", cohort=COHORT, as_of=t2)
        assert price is not None
        log.record("price_observation", price)

        pred = vintages[0] if vintages else None
        entries = []
        if pred is not None and price is not None:
            evaluation = evaluate_candidate(
                market="SPREAD", selection="HOME", line=price.line,
                american=price.american,
                model_probability=pred.spread_cover_prob,
                price_source=price.source,
                price_age_seconds=price_age_seconds(price, as_of=t2),
                policy=policy, data_completeness=pred.data_completeness,
            )
            entries.append(record_evaluation(
                s, prediction=pred, canonical_game_id=GAME, evaluation=evaluation,
                policy=policy, horizon=pred.horizon, as_of_at=t2,
                data_completeness=pred.data_completeness, data_mode=MODE,
            ))
        s.flush()
        log.record("research_evaluation", entries)
        log.record("simulated_fill", [e for e in entries if e.filled])

        # --- close --------------------------------------------------------- #
        captures = []
        if not skip_close:
            for market in policy.market_selection.markets:
                captures.append(capture_close(
                    s, canonical_game_id=GAME, market=market, kickoff_utc=KICK,
                    selection_rule=policy.closing_line.rule,
                    max_age_before_kickoff_minutes=(
                        policy.closing_line.max_age_before_kickoff_minutes),
                    cohort=COHORT, data_mode=MODE, provider_mode=ProviderMode.FIXTURE,
                    policy_version=POLICY, scheduled_slot=t_close, now=t_close,
                ).capture)
        s.flush()
        log.record("closing_capture", captures)

        # --- result, settlement, CLV, forward performance ------------------ #
        obs, disposition = ingest_result(
            s,
            ResultObservation(canonical_game_id=GAME, home_score=final_score[0],
                              away_score=final_score[1], observed_at=t_post),
            data_mode=MODE,
        )
        log.record("final_result", (obs, disposition))

        for entry in entries:
            settle_entry(s, entry=entry, home_score=final_score[0],
                         away_score=final_score[1], kickoff_utc=KICK,
                         policy=policy, data_mode=MODE)
        s.flush()
        log.record("settlement", [e for e in entries if e.result is not None])
        log.record("clv", [e for e in entries if e.clv_line is not None
                           or e.clv_probability is not None])
        log.record("forward_performance", [e for e in entries if e.settled_at is not None])
        log.record("closing_snapshot_used", closing_snapshot_for(
            s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT))
        log.record("closing_rule_version", CLOSING_RULE_VERSION)
        s.commit()

    return log


def predictions(factory: sessionmaker) -> list[ForwardPrediction]:
    from sqlalchemy import select

    with factory() as s:
        return list(s.scalars(select(ForwardPrediction).where(
            ForwardPrediction.canonical_game_id == GAME)))
