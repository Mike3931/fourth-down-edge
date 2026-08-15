"""The record chain driven by domain services, with no scheduler at all.

Deliberately NOT a `test_*` module: it is the harness the direct-chain and
parity tests share.

Why a second path exists. The scheduler-driven chain proves the operational
system works. It cannot prove the DOMAIN works, because every service is
reached through a handler that supplies its arguments - so a handler that
quietly passes the wrong cutoff, or reads a value the service should have
derived, produces a chain that looks correct end to end. Driving the same
services directly, from the same manifest, gives a second opinion from a
different caller.

Both paths read `scenario_manifest.py`. Neither owns a fixture of its own,
because the first attempt did and the two drifted: the scheduler generated
three prediction horizons and this chain generated one, and the parity gate
failed for a reason that had nothing to do with the system.

Nothing here imports `fde_api.forward.handlers` or constructs a
`Scheduler`. `test_chain_direct.py` proves that by parsing this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ForwardPrediction, ScheduleObservation
from fde_api.forward.closing import CLOSING_RULE_VERSION
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode
from scenario_manifest import COMPLETE_GAME, FixtureNws, ScenarioManifest

MANIFEST: ScenarioManifest = COMPLETE_GAME
KICK = MANIFEST.kickoff_utc
GAME = MANIFEST.canonical_game_id
POLICY = MANIFEST.policy_version
MODE = DataMode(MANIFEST.data_mode)
COHORT = Cohort(MANIFEST.cohort)
PROVIDER = ProviderMode(MANIFEST.provider_mode)


@dataclass
class DirectChainLog:
    """What each stage produced, for reporting and assertion."""

    manifest_hash: str = ""
    stages: dict[str, Any] = field(default_factory=dict)

    def record(self, stage: str, value: Any) -> Any:
        self.stages[stage] = value
        return value

    def summary(self) -> dict[str, Any]:
        return {k: (len(v) if isinstance(v, list) else bool(v))
                for k, v in self.stages.items()}


def seed(factory: sessionmaker) -> None:
    """Venues, the frozen policy, and the governed slate.

    Delegates to the shared seeder so both paths start from an identical
    baseline - a seeding difference would surface as a parity failure in
    records neither path actually computed.
    """
    from chainkit import seed_venues_and_policy

    seed_venues_and_policy(factory)


def _game(session: Session) -> ScheduleObservation:
    from fde_api.forward.schedule import current_schedule_state

    obs = current_schedule_state(session, GAME, MODE)
    assert obs is not None, "the slate was never seeded"
    return obs


def run_direct_chain(
    factory: sessionmaker,
    *,
    with_injuries: bool = True,
    manifest: ScenarioManifest = MANIFEST,
) -> DirectChainLog:
    """Execute the manifest through domain services, in knowable order."""
    from fde_api.db.forward_models import Venue
    from fde_api.forward.closing import capture_close, closing_snapshot_for
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

    m = manifest
    log = DirectChainLog(manifest_hash=m.content_hash)

    with factory() as s:
        policy = load_policy(s, m.policy_version)
        game = _game(s)
        log.record("schedule_observation", game)

        # --- odds, at every manifest slot -------------------------------- #
        for slot in m.odds_slots:
            capture_odds(
                s, m.payload_for(slot), provider_mode=PROVIDER,
                observed_at=slot.at, data_mode=MODE,
                # The same request identity the scheduler would use for this
                # slot, so the two paths attribute quotes identically.
                request_id=f"{m.canonical_game_id}:{slot.at.isoformat()}",
            )
        s.flush()
        log.record("odds_observations", list(m.odds_slot_times))

        # --- weather ------------------------------------------------------ #
        venue = s.get(Venue, game.stadium_id) if game.stadium_id else None
        vintages = []
        if venue is not None and venue.weather_applicable and venue.country == "US":
            for wv in m.weather_vintages:
                captured = capture_forecast_for_game(
                    s, canonical_game_id=GAME, venue=venue, kickoff_utc=m.kickoff_utc,
                    client=FixtureNws(wv), data_mode=MODE, observed_at=wv.at,
                )
                if captured is not None:
                    vintages.append(captured)
        s.flush()
        log.record("weather_vintage", vintages)

        # --- injuries ------------------------------------------------------ #
        if with_injuries:
            for v in m.injury_vintages:
                record_injury_observation(
                    s, canonical_game_id=GAME, team_id=v.team_id,
                    player_id=v.player_id, report_date=v.report_date,
                    observed_at=v.at,
                    source_category=SourceCategory(v.source_category),
                    practice_status=v.practice_status,
                    game_designation=v.game_designation, body_part=v.body_part,
                    source_reference="fixture injury report",
                    data_mode=MODE, now=v.at,
                )
        s.flush()
        log.record("injury_observations", observations_as_of(
            s, canonical_game_id=GAME, as_of_at=m.availability_cutoff, data_mode=MODE))

        # --- consensus, at every manifest cutoff --------------------------- #
        for market in policy.market_selection.markets:
            for cutoff in m.consensus_cutoffs:
                build_consensus(s, canonical_game_id=GAME, market=market,
                                as_of_at=cutoff, kickoff_utc=m.kickoff_utc,
                                cohort=COHORT, data_mode=MODE)
        s.flush()
        log.record("consensus_snapshot", list(m.consensus_cutoffs))

        # --- availability, at the manifest cutoff -------------------------- #
        # Assessed for every player with an observation at that cutoff -
        # exactly what `availability_computation` does - rather than for a
        # hand-listed set, which is how the two paths came to differ.
        assessments = []
        for o in observations_as_of(
            s, canonical_game_id=GAME, as_of_at=m.availability_cutoff, data_mode=MODE
        ):
            assessments.append(assess_player(
                s, canonical_game_id=GAME, team_id=o.team_id, player_id=o.player_id,
                as_of_at=m.availability_cutoff, cohort=COHORT, data_mode=MODE,
            ))
        s.flush()
        log.record("availability_assessment", assessments)
        log.record("feature_snapshot", resolve_starting_qb(
            s, canonical_game_id=GAME, team_id=m.home_team_id,
            expected_qb_id=m.home_qb_id, as_of_at=m.prediction_slot, data_mode=MODE))

        # --- predictions, every manifest horizon --------------------------- #
        preds = []
        for horizon in m.prediction_horizons:
            pred, _inputs = generate_vintage(
                s, game=game, horizon=horizon, policy=policy, moments=m.moments,
                cohort=COHORT, data_mode=MODE, now=m.prediction_slot,
                expected_home_qb=m.home_qb_id, expected_away_qb=m.away_qb_id,
            )
            if pred is not None:
                preds.append(pred)
        s.flush()
        log.record("prediction_vintage", preds)

        # --- prices -------------------------------------------------------- #
        for p in m.price_observations:
            record_price_observation(
                s,
                PriceObservation(
                    canonical_game_id=GAME, market=p.market, selection=p.selection,
                    line=p.line, american=p.american, observed_at=p.observed_at,
                    user_id=p.user_id, source=p.source, cohort=COHORT,
                    provider_mode=PROVIDER, policy_version=m.policy_version,
                    data_mode=MODE, confirmed=p.confirmed,
                ),
                now=m.price_evaluation_slot,
            )
        s.flush()
        log.record("price_observation", [
            current_price(s, canonical_game_id=GAME, market=p.market,
                          selection=p.selection, cohort=COHORT,
                          as_of=m.price_evaluation_slot)
            for p in m.price_observations
        ])

        # --- price-specific evaluation ------------------------------------- #
        # The newest vintage at or before the evaluation cutoff, matching
        # `price_evaluation`'s selection rule exactly.
        latest = _latest_vintage(preds, m.price_evaluation_slot)
        # NOTE: the scheduler's `price_evaluation` also applies a Data Health
        # gate. Applying it here was tried and reverted: the direct database
        # has no scheduler runs, so its health report differs, suppression
        # fires, and every evaluation collapses to DATA_INCOMPLETE - losing
        # the fill and the CLV this chain exists to exercise. Closing that
        # asymmetry means making the health context comparable between the
        # two databases, not bolting the gate on here. Recorded as the
        # remaining parity gap rather than papered over.
        entries = []
        if latest is not None:
            for market in policy.market_selection.markets:
                for selection in _selections(market):
                    price = current_price(
                        s, canonical_game_id=GAME, market=market,
                        selection=selection, cohort=COHORT,
                        as_of=m.price_evaluation_slot,
                    )
                    if price is None:
                        continue
                    evaluation = evaluate_candidate(
                        market=market, selection=selection, line=price.line,
                        american=price.american,
                        model_probability=_model_probability(latest, market, selection),
                        price_source=price.source,
                        price_age_seconds=price_age_seconds(
                            price, as_of=m.price_evaluation_slot),
                        policy=policy, data_completeness=latest.data_completeness,
                    )
                    entries.append(record_evaluation(
                        s, prediction=latest, canonical_game_id=GAME,
                        evaluation=evaluation, policy=policy, horizon=latest.horizon,
                        as_of_at=m.price_evaluation_slot,
                        data_completeness=latest.data_completeness, cohort=COHORT, data_mode=MODE,
                    ))
        s.flush()
        log.record("research_evaluation", entries)
        log.record("simulated_fill", [e for e in entries if e.filled])

        # --- close ---------------------------------------------------------- #
        captures = []
        for market in policy.market_selection.markets:
            captures.append(capture_close(
                s, canonical_game_id=GAME, market=market, kickoff_utc=m.kickoff_utc,
                selection_rule=policy.closing_line.rule,
                max_age_before_kickoff_minutes=(
                    policy.closing_line.max_age_before_kickoff_minutes),
                cohort=COHORT, data_mode=MODE, provider_mode=PROVIDER,
                policy_version=m.policy_version, scheduled_slot=m.closing_slot,
                now=m.closing_slot,
            ).capture)
        s.flush()
        log.record("closing_capture", captures)

        # --- result, settlement, CLV, forward performance -------------------- #
        obs, disposition = ingest_result(
            s,
            ResultObservation(canonical_game_id=GAME, home_score=m.home_score,
                              away_score=m.away_score,
                              observed_at=m.result_observed_at),
            data_mode=MODE,
        )
        log.record("final_result", (obs, disposition))

        for entry in entries:
            settle_entry(s, entry=entry, home_score=m.home_score,
                         away_score=m.away_score, kickoff_utc=m.kickoff_utc,
                         policy=policy, data_mode=MODE)
        s.flush()
        log.record("settlement", [e for e in entries if e.result is not None])
        log.record("clv", [e for e in entries if e.clv_line is not None
                           or e.clv_probability is not None])
        log.record("forward_performance",
                   [e for e in entries if e.settled_at is not None])
        log.record("closing_snapshot_used", closing_snapshot_for(
            s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT))
        log.record("closing_rule_version", CLOSING_RULE_VERSION)
        s.commit()

    return log


def _latest_vintage(
    preds: list[ForwardPrediction], as_of: datetime
) -> ForwardPrediction | None:
    """The newest vintage at or before `as_of`, ties broken by id.

    The same rule `price_evaluation` applies. Written out rather than
    imported from the handler, because importing it would make this chain
    depend on the scheduler it exists to be independent of.
    """
    eligible = [p for p in preds if p.as_of_at <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda p: (p.as_of_at, p.id))


def _selections(market: str) -> tuple[str, ...]:
    return {
        "SPREAD": ("HOME", "AWAY"),
        "TOTAL": ("OVER", "UNDER"),
        "MONEYLINE": ("HOME", "AWAY"),
    }.get(market, ())


def _model_probability(
    pred: ForwardPrediction, market: str, selection: str
) -> float | None:
    """The model's probability for one priced selection.

    Mirrors the handler's mapping. Complements are derived from the stored
    side rather than stored twice, so the two can never disagree.
    """
    if market == "SPREAD":
        p = pred.spread_cover_prob
        return p if selection == "HOME" else (None if p is None else 1.0 - p)
    if market == "TOTAL":
        p = pred.total_over_prob
        return p if selection == "OVER" else (None if p is None else 1.0 - p)
    if market == "MONEYLINE":
        p = pred.home_win_prob
        return p if selection == "HOME" else (None if p is None else 1.0 - p)
    return None


def predictions(factory: sessionmaker) -> list[ForwardPrediction]:
    from sqlalchemy import select

    with factory() as s:
        return list(s.scalars(select(ForwardPrediction).where(
            ForwardPrediction.canonical_game_id == GAME)))
