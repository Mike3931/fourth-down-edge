"""The authoritative record chain, and reconciliation of two histories.

A game produces two independent histories. The SCHEDULER history says what
ran: which jobs, when, with what outcome. The DOMAIN history says what
exists: observations, snapshots, vintages, evaluations, settlements. Each
is written by a different code path, and neither is evidence for the other.

That is the whole point. A scheduler row that says SUCCESS while the domain
holds nothing is the signature of a handler that returned without writing.
A domain record with no completed run behind it is the signature of a crash
after commit. Reading only one history makes both invisible.

`reconcile_chain` compares them and returns a typed verdict. It never
repairs an ambiguous terminal record: a duplicate settlement or a
conflicting result is a question about money-shaped facts, and guessing at
those is worse than reporting them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import (
    AvailabilityAssessment,
    ClosingCapture,
    ConsensusSnapshot,
    ForwardLedgerEntry,
    ForwardPrediction,
    InjuryObservation,
    ManualBookPriceEntry,
    OddsQuote,
    ScheduledJobRun,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.forward.state import Outcome

# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #


class Stage(StrEnum):
    """The stages a game traverses, in the order they become knowable.

    Order is not decoration: it is the point-in-time ordering. A stage may
    only read from stages before it, so the sequence is what makes
    "prediction cannot see the result" a structural property rather than a
    rule someone has to remember.
    """

    SCHEDULE_OBSERVATION = "schedule_observation"
    ODDS_OBSERVATIONS = "odds_observations"
    CONSENSUS_SNAPSHOT = "consensus_snapshot"
    WEATHER_VINTAGE = "weather_vintage"
    INJURY_OBSERVATIONS = "injury_observations"
    AVAILABILITY_ASSESSMENT = "availability_assessment"
    FEATURE_SNAPSHOT = "feature_snapshot"
    PREDICTION_VINTAGE = "prediction_vintage"
    RESEARCH_EVALUATION = "research_evaluation"
    PRICE_OBSERVATION = "price_observation"
    PRICE_EVALUATION = "price_specific_evaluation"
    SIMULATED_FILL = "simulated_fill"
    CLOSING_CAPTURE = "closing_capture"
    FINAL_RESULT = "final_result"
    SETTLEMENT = "settlement"
    CLV = "clv"
    FORWARD_PERFORMANCE = "forward_performance_record"


CHAIN_ORDER: tuple[Stage, ...] = tuple(Stage)

# Stages that a game can legitimately lack without the chain being broken.
# Weather is the clear case: an indoor or international venue has no
# applicable forecast, and inventing one would be worse than its absence.
CONDITIONALLY_ABSENT: frozenset[Stage] = frozenset({
    Stage.WEATHER_VINTAGE,
    Stage.SIMULATED_FILL,   # a PASS or WATCH is never filled
    Stage.CLOSING_CAPTURE,  # a missing close is explicit, not an error
    Stage.CLV,              # no close means no CLV
})

# Which scheduler job is expected to have produced each stage. Used by
# reconciliation to pair the two histories; a stage with no job is produced
# as a side effect of another stage's job.
STAGE_JOBS: dict[Stage, str | None] = {
    Stage.SCHEDULE_OBSERVATION: "schedule_refresh",
    Stage.ODDS_OBSERVATIONS: "odds_capture",
    Stage.CONSENSUS_SNAPSHOT: "consensus_build",
    Stage.WEATHER_VINTAGE: "weather_capture",
    Stage.INJURY_OBSERVATIONS: "injury_reconciliation",
    Stage.AVAILABILITY_ASSESSMENT: "availability_computation",
    Stage.FEATURE_SNAPSHOT: "feature_snapshot",
    Stage.PREDICTION_VINTAGE: "prediction_vintage",
    Stage.RESEARCH_EVALUATION: "price_evaluation",
    Stage.PRICE_OBSERVATION: "price_observation",
    Stage.PRICE_EVALUATION: "price_evaluation",
    Stage.SIMULATED_FILL: "price_evaluation",   # written by record_evaluation
    Stage.CLOSING_CAPTURE: "closing_capture",
    Stage.FINAL_RESULT: "result_ingestion",
    Stage.SETTLEMENT: "settlement",
    Stage.CLV: "settlement",
    Stage.FORWARD_PERFORMANCE: "forward_evaluation",
}


class ChainVerdict(StrEnum):
    """The typed outcome of reconciliation.

    Ordered by severity so a caller can compare. CORRUPTED is not
    "worse INCOMPLETE": it means the two histories disagree about something
    that cannot both be true, which no amount of re-running fixes.
    """

    CONSISTENT = "CONSISTENT"
    INCOMPLETE = "INCOMPLETE"
    RECOVERABLE = "RECOVERABLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    CORRUPTED = "CORRUPTED"


_SEVERITY: dict[ChainVerdict, int] = {
    ChainVerdict.CONSISTENT: 0,
    ChainVerdict.INCOMPLETE: 1,
    ChainVerdict.RECOVERABLE: 2,
    ChainVerdict.MANUAL_REVIEW: 3,
    ChainVerdict.CORRUPTED: 4,
}


@dataclass
class ChainFinding:
    """One disagreement between the two histories, or one gap in either."""

    check: str
    verdict: ChainVerdict
    stage: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "verdict": self.verdict.value,
            "stage": self.stage,
            "detail": self.detail,
        }


@dataclass
class ChainState:
    """What the DOMAIN holds for one game in one cohort."""

    canonical_game_id: str
    data_mode: str
    policy_version: str | None
    stages: dict[Stage, int] = field(default_factory=dict)
    identifiers: dict[Stage, list[str]] = field(default_factory=dict)

    def present(self, stage: Stage) -> bool:
        return self.stages.get(stage, 0) > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_game_id": self.canonical_game_id,
            "data_mode": self.data_mode,
            "policy_version": self.policy_version,
            "stages": {s.value: self.stages.get(s, 0) for s in CHAIN_ORDER},
        }


# --------------------------------------------------------------------------- #
# Reading the domain history
# --------------------------------------------------------------------------- #


def read_chain(
    session: Session,
    *,
    canonical_game_id: str,
    data_mode: str,
    policy_version: str | None = None,
) -> ChainState:
    """Count what exists at each stage for one game.

    Every query is scoped by `data_mode`. Without that scoping a demo row
    would count as evidence about a burn-in chain, which is the cohort-
    mixing this whole design exists to prevent.
    """
    state = ChainState(
        canonical_game_id=canonical_game_id,
        data_mode=data_mode,
        policy_version=policy_version,
    )

    def count(stage: Stage, stmt: Any) -> None:
        ids = [str(i) for i in session.scalars(stmt)]
        state.stages[stage] = len(ids)
        state.identifiers[stage] = ids[:50]

    g, dm = canonical_game_id, data_mode

    count(Stage.SCHEDULE_OBSERVATION, select(ScheduleObservation.id).where(
        ScheduleObservation.canonical_game_id == g, ScheduleObservation.data_mode == dm))
    count(Stage.ODDS_OBSERVATIONS, select(OddsQuote.id).where(
        OddsQuote.canonical_game_id == g, OddsQuote.data_mode == dm))
    count(Stage.CONSENSUS_SNAPSHOT, select(ConsensusSnapshot.id).where(
        ConsensusSnapshot.canonical_game_id == g, ConsensusSnapshot.data_mode == dm))
    count(Stage.WEATHER_VINTAGE, select(WeatherForecastVintage.id).where(
        WeatherForecastVintage.canonical_game_id == g,
        WeatherForecastVintage.data_mode == dm))
    count(Stage.INJURY_OBSERVATIONS, select(InjuryObservation.id).where(
        InjuryObservation.canonical_game_id == g, InjuryObservation.data_mode == dm))
    count(Stage.AVAILABILITY_ASSESSMENT, select(AvailabilityAssessment.id).where(
        AvailabilityAssessment.canonical_game_id == g,
        AvailabilityAssessment.data_mode == dm))

    # The feature snapshot is not a table of its own: it is the input set a
    # prediction vintage recorded in its lineage. Counting predictions that
    # carry lineage is therefore the honest measure of whether the feature
    # stage happened, rather than inventing a row to count.
    preds = list(session.scalars(select(ForwardPrediction).where(
        ForwardPrediction.canonical_game_id == g, ForwardPrediction.data_mode == dm)))
    state.stages[Stage.FEATURE_SNAPSHOT] = sum(1 for p in preds if p.lineage)
    state.identifiers[Stage.FEATURE_SNAPSHOT] = [p.id for p in preds if p.lineage][:50]
    state.stages[Stage.PREDICTION_VINTAGE] = len(preds)
    state.identifiers[Stage.PREDICTION_VINTAGE] = [p.id for p in preds][:50]

    count(Stage.PRICE_OBSERVATION, select(ManualBookPriceEntry.id).where(
        ManualBookPriceEntry.canonical_game_id == g,
        ManualBookPriceEntry.data_mode == dm))

    ledger_stmt = select(ForwardLedgerEntry).where(
        ForwardLedgerEntry.canonical_game_id == g, ForwardLedgerEntry.data_mode == dm)
    if policy_version:
        ledger_stmt = ledger_stmt.where(ForwardLedgerEntry.policy_version == policy_version)
    ledger = list(session.scalars(ledger_stmt))

    state.stages[Stage.RESEARCH_EVALUATION] = len(ledger)
    state.identifiers[Stage.RESEARCH_EVALUATION] = [str(e.id) for e in ledger][:50]
    priced = [e for e in ledger if e.qualifying_american is not None]
    state.stages[Stage.PRICE_EVALUATION] = len(priced)
    state.identifiers[Stage.PRICE_EVALUATION] = [str(e.id) for e in priced][:50]
    filled = [e for e in ledger if e.filled]
    state.stages[Stage.SIMULATED_FILL] = len(filled)
    state.identifiers[Stage.SIMULATED_FILL] = [str(e.id) for e in filled][:50]
    settled = [e for e in ledger if e.result is not None]
    state.stages[Stage.SETTLEMENT] = len(settled)
    state.identifiers[Stage.SETTLEMENT] = [str(e.id) for e in settled][:50]
    with_clv = [e for e in ledger if e.clv_probability is not None or e.clv_line is not None]
    state.stages[Stage.CLV] = len(with_clv)
    state.identifiers[Stage.CLV] = [str(e.id) for e in with_clv][:50]
    # The forward-performance record is the ledger row itself once it has
    # travelled the whole chain: it is not a separate table, and pretending
    # otherwise would mean writing the same facts twice.
    performance = [e for e in ledger if e.settled_at is not None or e.exclusion_reason]
    state.stages[Stage.FORWARD_PERFORMANCE] = len(performance)
    state.identifiers[Stage.FORWARD_PERFORMANCE] = [str(e.id) for e in performance][:50]

    # The close is its own record now. A MISSING capture still counts as a
    # close having been SOUGHT and answered, which is the distinction that
    # matters: "we looked and there was nothing" is a result, and treating
    # it as absence would make an explicit answer indistinguishable from
    # never having asked.
    count(Stage.CLOSING_CAPTURE, select(ClosingCapture.id).where(
        ClosingCapture.canonical_game_id == g, ClosingCapture.data_mode == dm))

    obs = list(session.scalars(select(ScheduleObservation).where(
        ScheduleObservation.canonical_game_id == g, ScheduleObservation.data_mode == dm)))
    final = [o for o in obs if o.game_status == "FINAL"]
    state.stages[Stage.FINAL_RESULT] = len(final)
    state.identifiers[Stage.FINAL_RESULT] = [str(o.id) for o in final][:50]

    return state


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


def _scheduler_history(
    session: Session, *, data_mode: str, policy_version: str | None
) -> dict[str, list[ScheduledJobRun]]:
    runs = list(session.scalars(
        select(ScheduledJobRun).where(ScheduledJobRun.data_mode == data_mode)
    ))
    by_job: dict[str, list[ScheduledJobRun]] = {}
    for r in runs:
        by_job.setdefault(r.job_kind, []).append(r)
    return by_job


_SUCCESS = {Outcome.SUCCESS.value, Outcome.SUCCESS_WITH_WARNINGS.value}


def reconcile_chain(
    session: Session,
    *,
    canonical_game_id: str,
    data_mode: str,
    policy_version: str | None = None,
    weather_applicable: bool = True,
    expect_close: bool = True,
    expect_settlement: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare scheduler history with domain history for one game.

    Returns a verdict and every finding behind it. Ambiguous terminal
    records are reported, never repaired: a conflicting settlement is a
    question about a money-shaped fact, and a service that guesses at those
    is worse than one that stops.
    """
    state = read_chain(
        session, canonical_game_id=canonical_game_id, data_mode=data_mode,
        policy_version=policy_version,
    )
    by_job = _scheduler_history(session, data_mode=data_mode, policy_version=policy_version)
    findings: list[ChainFinding] = []

    def add(check: str, verdict: ChainVerdict, stage: Stage | None, detail: str) -> None:
        findings.append(ChainFinding(check, verdict, stage.value if stage else None, detail))

    # --- the two histories, compared in both directions ------------------ #
    for stage in CHAIN_ORDER:
        job = STAGE_JOBS.get(stage)
        if job is None:
            continue
        succeeded = any(r.job_outcome in _SUCCESS for r in by_job.get(job, []))
        present = state.present(stage)

        if succeeded and not present and stage not in CONDITIONALLY_ABSENT:
            add("scheduler_success_without_domain_effect", ChainVerdict.RECOVERABLE, stage,
                f"{job} reported success but no {stage.value} exists for {canonical_game_id}")
        if present and not succeeded:
            # The signature of a crash after commit: real records, no
            # completed run. Recoverable, because the effect is already
            # there and the run row is what is missing.
            add("domain_effect_without_scheduler_completion", ChainVerdict.RECOVERABLE, stage,
                f"{stage.value} exists but no successful {job} run is recorded")

    # --- completeness ---------------------------------------------------- #
    for stage in CHAIN_ORDER:
        if state.present(stage):
            continue
        if stage is Stage.WEATHER_VINTAGE and not weather_applicable:
            continue  # indoor or international: absence is correct
        if stage is Stage.CLOSING_CAPTURE and not expect_close:
            add("missing_close", ChainVerdict.INCOMPLETE, stage,
                "no closing capture; recorded as explicitly missing")
            continue
        if stage is Stage.SETTLEMENT and not expect_settlement:
            continue
        if stage is Stage.CLV and not (expect_close and state.present(Stage.CLOSING_CAPTURE)):
            continue
        if stage is Stage.SIMULATED_FILL:
            continue  # a PASS or WATCH is never filled; absence is a result
        verdict = ChainVerdict.INCOMPLETE
        add(f"missing_{stage.value}", verdict, stage, f"no {stage.value} for {canonical_game_id}")

    # --- terminal ambiguity ---------------------------------------------- #
    ledger_stmt = select(ForwardLedgerEntry).where(
        ForwardLedgerEntry.canonical_game_id == canonical_game_id,
        ForwardLedgerEntry.data_mode == data_mode,
    )
    if policy_version:
        ledger_stmt = ledger_stmt.where(ForwardLedgerEntry.policy_version == policy_version)
    ledger = list(session.scalars(ledger_stmt))

    by_selection: dict[tuple[str, str, str], list[ForwardLedgerEntry]] = {}
    for e in ledger:
        by_selection.setdefault((e.market, e.selection or "", e.horizon), []).append(e)

    for key, rows in by_selection.items():
        results = {r.result for r in rows if r.result is not None}
        if len(results) > 1:
            # Two settlements of the same selection disagreeing is the case
            # that must never be auto-repaired.
            add("conflicting_settlement", ChainVerdict.MANUAL_REVIEW, Stage.SETTLEMENT,
                f"{key} has conflicting results {sorted(results)}")
        settled = [r for r in rows if r.result is not None]
        if settled and len(settled) < len([r for r in rows if r.filled]):
            add("partial_settlement", ChainVerdict.MANUAL_REVIEW, Stage.SETTLEMENT,
                f"{key}: {len(settled)} of {len([r for r in rows if r.filled])} "
                "filled entries settled")

    # --- duplicates ------------------------------------------------------ #
    seen: dict[tuple[Any, ...], int] = {}
    for e in ledger:
        k = (e.market, e.selection, e.horizon, e.as_of_at, e.policy_version)
        seen[k] = seen.get(k, 0) + 1
    dupes = [k for k, n in seen.items() if n > 1]
    if dupes:
        add("duplicate_effects", ChainVerdict.CORRUPTED, Stage.PRICE_EVALUATION,
            f"{len(dupes)} evaluation identity(ies) appear more than once")

    # --- governance consistency ------------------------------------------ #
    # A `cohort_mismatch` check used to sit here, comparing
    # `{e.data_mode for e in ledger}` against a length of one. It could
    # never fire: `ledger_stmt` above filters `data_mode == data_mode`
    # unconditionally, so that set has at most one member by construction.
    # It read as coverage of a corruption class and provided none.
    #
    # It is removed rather than widened. Widening means querying the game
    # across ALL modes, and whether a game legitimately holds both DEMO and
    # LIVE_RESEARCH ledger rows is a design question this function is not
    # the place to settle — a game replayed in demo and then captured live
    # would trip it on every call. Deciding that is a deliberate change to
    # what "corrupted" means, not a repair.
    #
    # The policy check below is NOT vacuous: `policy_version` filters only
    # when one is supplied, so an unscoped call genuinely spans policies.
    policies = {e.policy_version for e in ledger}
    if len(policies) > 1:
        add("policy_mismatch", ChainVerdict.CORRUPTED, None,
            f"ledger rows for one game span policies {sorted(p or '' for p in policies)}")

    quote_modes = {
        q.provider_mode for q in session.scalars(select(OddsQuote).where(
            OddsQuote.canonical_game_id == canonical_game_id,
            OddsQuote.data_mode == data_mode,
        ))
    }
    if len(quote_modes) > 1:
        add("provider_mode_mismatch", ChainVerdict.MANUAL_REVIEW, Stage.ODDS_OBSERVATIONS,
            f"quotes for one game span provider modes {sorted(quote_modes)}")

    # --- lineage --------------------------------------------------------- #
    for p in session.scalars(select(ForwardPrediction).where(
        ForwardPrediction.canonical_game_id == canonical_game_id,
        ForwardPrediction.data_mode == data_mode,
    )):
        if not p.lineage:
            add("missing_lineage", ChainVerdict.MANUAL_REVIEW, Stage.PREDICTION_VINTAGE,
                f"prediction {p.id} carries no input lineage")

    verdict = max(
        (f.verdict for f in findings), key=lambda v: _SEVERITY[v], default=ChainVerdict.CONSISTENT
    )
    return {
        "canonical_game_id": canonical_game_id,
        "data_mode": data_mode,
        "policy_version": policy_version,
        "verdict": verdict.value,
        "findings": [f.as_dict() for f in findings],
        "state": state.as_dict(),
        "checked_at": (now.isoformat() if now else None),
        "auto_repair": False,
        "auto_repair_note": (
            "ambiguous terminal records are reported, never repaired; a conflicting "
            "or partial settlement requires a human"
        ),
    }


# --------------------------------------------------------------------------- #
# Semantic chain hash
# --------------------------------------------------------------------------- #


def chain_semantic_hash(state: ChainState) -> str:
    """A stable hash of the chain's SHAPE, not of its identifiers.

    Row ids and timestamps differ on every run, so hashing them would
    produce a value that changes for no reason and therefore detects
    nothing. This hashes the stage-by-stage counts, which is what "the same
    game traversed the same chain" actually means. Two runs of the same
    fixture must agree; a run that skipped a stage must not.
    """
    body = {
        "canonical_game_id": state.canonical_game_id,
        "data_mode": state.data_mode,
        "stages": {s.value: state.stages.get(s, 0) for s in CHAIN_ORDER},
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
