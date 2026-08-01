"""Forward evaluation cohort: research candidates, CLV, and settlement.

This ledger is deliberately separate from the historical backtest tables.
Mixing them would let a favourable historical sample flatter a forward
result, so the two never appear in the same query, metric, or report.

Every evaluated opportunity is retained — RESEARCH_CANDIDATE, WATCH,
PASS, and DATA_INCOMPLETE alike. A forward test that quietly drops the
opportunities it declined cannot be audited.

CLV is defined here, before capture begins:
  * Spread/total: compare the simulated accepted LINE against the
    rule-selected consensus closing line. When the lines are equal, fall
    back to comparing no-vig prices. Line CLV and probability CLV are
    reported separately, never blended into one flattering number.
  * Moneyline: compare no-vig implied probability at simulated execution
    against the no-vig probability of the predetermined close.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.backtest.execution import ExecutionModel, break_even_prob
from fde_api.db.forward_models import ForwardLedgerEntry, ForwardPrediction
from fde_api.forward.consensus import american_to_prob, closing_consensus, no_vig_two_way
from fde_api.forward.modes import DataMode
from fde_api.forward.policy import ForwardTestPolicy
from fde_api.util import utc_now

Status = Literal["RESEARCH_CANDIDATE", "WATCH", "PASS", "DATA_INCOMPLETE"]


@dataclass
class CandidateEvaluation:
    """One price-specific evaluation, with every field the UI must show."""

    market: str
    selection: str
    line: float | None
    american: int
    price_source: str
    price_age_seconds: int | None
    model_probability: float | None
    conservative_probability: float | None
    break_even_probability: float
    expected_value: float | None
    fair_american: int | None
    target_american: int | None
    invalidation_american: int | None
    status: Status
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "selection": self.selection,
            "line": self.line,
            "american": self.american,
            "price_source": self.price_source,
            "price_age_seconds": self.price_age_seconds,
            "model_probability": self.model_probability,
            "conservative_probability": self.conservative_probability,
            "break_even_probability": self.break_even_probability,
            "expected_value": self.expected_value,
            "fair_american": self.fair_american,
            "target_american": self.target_american,
            "invalidation_american": self.invalidation_american,
            "status": self.status,
            "reasons": self.reasons,
        }


def prob_to_american(p: float) -> int:
    """Fair American price for a probability."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


# Uncertainty haircut applied to the model probability before comparing to
# break-even. Fixed by policy; never tuned against forward results.
UNCERTAINTY_HAIRCUT = 0.02


def evaluate_candidate(
    *,
    market: str,
    selection: str,
    line: float | None,
    american: int,
    model_probability: float | None,
    price_source: str,
    price_age_seconds: int | None,
    policy: ForwardTestPolicy,
    data_completeness: float,
    extra_reasons: list[str] | None = None,
) -> CandidateEvaluation:
    """Apply the frozen research-candidate rules to one price."""
    reasons = list(extra_reasons or [])
    be = break_even_prob(american)

    if model_probability is None:
        reasons.append("no model probability at this cutoff")
        return CandidateEvaluation(
            market, selection, line, american, price_source, price_age_seconds,
            None, None, be, None, None, None, None, "DATA_INCOMPLETE", reasons,
        )

    if data_completeness < policy.missing_data.min_data_completeness_for_candidate:
        reasons.append(
            f"data completeness {data_completeness:.2f} below policy minimum "
            f"{policy.missing_data.min_data_completeness_for_candidate:.2f}"
        )
        conservative = max(0.0, model_probability - UNCERTAINTY_HAIRCUT)
        return CandidateEvaluation(
            market, selection, line, american, price_source, price_age_seconds,
            model_probability, conservative, be, None,
            prob_to_american(model_probability), None, None, "DATA_INCOMPLETE", reasons,
        )

    if (
        price_age_seconds is not None
        and price_age_seconds > policy.price_staleness.max_consensus_age_minutes * 60
    ):
        reasons.append(f"price age {price_age_seconds}s exceeds policy staleness limit")
        return CandidateEvaluation(
            market, selection, line, american, price_source, price_age_seconds,
            model_probability, None, be, None, prob_to_american(model_probability),
            None, None, "DATA_INCOMPLETE", reasons,
        )

    conservative = max(0.0, model_probability - UNCERTAINTY_HAIRCUT)
    edge = conservative - be
    dec = 1 + (american / 100 if american > 0 else 100 / -american)
    ev = conservative * (dec - 1) - (1 - conservative)

    threshold = policy.research_candidate_edge_threshold
    fair = prob_to_american(model_probability)
    # Target: the price at which the edge would reach the threshold.
    target = prob_to_american(min(0.999, conservative - threshold + 1e-9))
    # Invalidation: the price at which the edge disappears entirely.
    invalidation = prob_to_american(conservative)

    if edge >= threshold:
        status: Status = "RESEARCH_CANDIDATE"
        reasons.append(
            f"conservative probability {conservative:.3f} exceeds break-even {be:.3f} "
            f"by {edge:.3f}, at or above the {threshold:.3f} policy threshold"
        )
    elif edge >= threshold - policy.watch_band_width:
        status = "WATCH"
        reasons.append(
            f"edge {edge:.3f} is within {policy.watch_band_width:.3f} of the "
            f"{threshold:.3f} threshold but does not meet it"
        )
    else:
        status = "PASS"
        reasons.append(f"edge {edge:.3f} is below the {threshold:.3f} policy threshold")

    return CandidateEvaluation(
        market, selection, line, american, price_source, price_age_seconds,
        model_probability, conservative, be, ev, fair, target, invalidation, status, reasons,
    )


def record_evaluation(
    session: Session,
    *,
    prediction: ForwardPrediction | None,
    canonical_game_id: str,
    evaluation: CandidateEvaluation,
    policy: ForwardTestPolicy,
    horizon: str,
    as_of_at: datetime,
    data_completeness: float | None,
    exclusion_reason: str | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> ForwardLedgerEntry:
    """Append a ledger row, simulating execution for candidates only.

    Non-candidates are recorded too, with no simulated fill — the ledger
    must show what was declined, not just what was taken.
    """
    now = utc_now()
    entry = ForwardLedgerEntry(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        forward_prediction_id=prediction.id if prediction else None,
        policy_version=policy.policy_version,
        model_version=policy.model_version,
        horizon=horizon,
        market=evaluation.market,
        selection=evaluation.selection,
        status=evaluation.status,
        qualifying_line=evaluation.line,
        qualifying_american=evaluation.american,
        price_source=evaluation.price_source,
        price_age_seconds=evaluation.price_age_seconds,
        model_probability=evaluation.model_probability,
        conservative_probability=evaluation.conservative_probability,
        break_even_probability=evaluation.break_even_probability,
        expected_value=evaluation.expected_value,
        data_completeness=data_completeness,
        exclusion_reason=exclusion_reason,
        reasons={"reasons": evaluation.reasons, "fair_american": evaluation.fair_american,
                 "target_american": evaluation.target_american,
                 "invalidation_american": evaluation.invalidation_american},
        as_of_at=as_of_at,
        created_at=now,
    )

    if evaluation.status == "RESEARCH_CANDIDATE" and exclusion_reason is None:
        model = ExecutionModel(_execution_config(policy))
        fill = model.attempt_fill(
            game_id=canonical_game_id,
            market=evaluation.market,
            selection=evaluation.selection,  # type: ignore[arg-type]
            line=evaluation.line,
            price_american=evaluation.american,
        )
        entry.filled = fill.filled
        entry.fill_note = fill.reason
        entry.simulated_delay_seconds = policy.execution.manual_entry_delay_seconds
        if fill.filled:
            entry.simulated_line = fill.line
            entry.simulated_american = fill.price_american

    session.add(entry)
    session.flush()
    return entry


def _execution_config(policy: ForwardTestPolicy):
    from fde_api.backtest.execution import ExecutionConfig

    e = policy.execution
    return ExecutionConfig(
        manual_delay_minutes=e.manual_entry_delay_seconds / 60.0,
        price_deterioration_cents=e.price_deterioration_cents,
        halfpoint_deterioration_prob=e.halfpoint_deterioration_probability,
        no_fill_prob=e.no_fill_probability,
        suspended_prob=e.suspended_probability,
        seed=e.seed,
    )


# --------------------------------------------------------------------------- #
# CLV — defined before capture, computed only from the rule-selected close
# --------------------------------------------------------------------------- #


@dataclass
class ClvResult:
    line_clv: float | None
    probability_clv: float | None
    closing_line: float | None
    closing_american: int | None
    closing_no_vig_prob: float | None
    note: str


def compute_clv(
    session: Session,
    *,
    entry: ForwardLedgerEntry,
    kickoff_utc: datetime,
    policy: ForwardTestPolicy,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> ClvResult:
    """CLV for one filled entry against the predetermined close."""
    if not entry.filled:
        return ClvResult(None, None, None, None, None, "not filled; CLV not applicable")

    snap = closing_consensus(
        session,
        canonical_game_id=entry.canonical_game_id,
        market=entry.market,
        kickoff_utc=kickoff_utc,
        max_age_before_kickoff_minutes=policy.closing_line.max_age_before_kickoff_minutes,
        data_mode=data_mode,
    )
    if snap is None:
        return ClvResult(None, None, None, None, None,
                         "no eligible closing consensus within the policy window; CLV unavailable")

    exec_price = entry.simulated_american or entry.qualifying_american
    exec_line = entry.simulated_line if entry.simulated_line is not None else entry.qualifying_line

    if entry.market == "MONEYLINE":
        if snap.home_price_american is None or snap.away_price_american is None:
            return ClvResult(None, None, None, None, None, "closing moneyline incomplete")
        nv_home, nv_away = no_vig_two_way(snap.home_price_american, snap.away_price_american)
        close_prob = nv_home if entry.selection == "HOME" else nv_away
        exec_prob = american_to_prob(exec_price) if exec_price else None
        # Positive when we secured a price implying LESS probability than the
        # market ultimately settled on for our side.
        prob_clv = (close_prob - exec_prob) if exec_prob is not None else None
        close_american = snap.home_price_american if entry.selection == "HOME" else snap.away_price_american
        return ClvResult(None, prob_clv, None, close_american, close_prob,
                         "moneyline CLV compares no-vig implied probabilities")

    close_line = snap.median_line
    if close_line is None or exec_line is None:
        return ClvResult(None, None, close_line, None, None, "closing line unavailable")

    # Line CLV in points, signed so positive always favours our side.
    if entry.market == "SPREAD":
        line_clv = (exec_line - close_line) if entry.selection == "HOME" else (close_line - exec_line)
        close_price = snap.home_price_american if entry.selection == "HOME" else snap.away_price_american
    else:  # TOTAL
        line_clv = (close_line - exec_line) if entry.selection == "OVER" else (exec_line - close_line)
        close_price = snap.over_price_american if entry.selection == "OVER" else snap.under_price_american

    prob_clv = None
    note = "line CLV in points"
    if abs(line_clv) < 1e-9 and close_price is not None and exec_price is not None:
        # Equal lines: the price is the only differentiator (policy rule).
        prob_clv = american_to_prob(close_price) - american_to_prob(exec_price)
        note = "lines equal; CLV computed from no-vig price difference"

    return ClvResult(line_clv, prob_clv, close_line, close_price, None, note)


def settle_entry(
    session: Session,
    *,
    entry: ForwardLedgerEntry,
    home_score: int,
    away_score: int,
    kickoff_utc: datetime,
    policy: ForwardTestPolicy,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> ForwardLedgerEntry:
    """Settle a filled entry and attach CLV. Idempotent."""
    if entry.result is not None:
        return entry
    if not entry.filled:
        entry.result = "NO_FILL"
        entry.pnl_units = 0.0
        entry.settled_at = utc_now()
        session.flush()
        return entry

    settlement = ExecutionModel.settle(
        market=entry.market,
        selection=entry.selection,  # type: ignore[arg-type]
        line=entry.simulated_line if entry.simulated_line is not None else entry.qualifying_line,
        price_american=entry.simulated_american or entry.qualifying_american or -110,
        home_score=home_score,
        away_score=away_score,
    )
    entry.result = settlement.result
    entry.pnl_units = settlement.pnl_units * policy.bankroll.stake_per_candidate_units

    clv = compute_clv(session, entry=entry, kickoff_utc=kickoff_utc, policy=policy, data_mode=data_mode)
    entry.clv_line = clv.line_clv
    entry.clv_probability = clv.probability_clv
    entry.closing_line = clv.closing_line
    entry.closing_american = clv.closing_american
    entry.closing_no_vig_prob = clv.closing_no_vig_prob
    entry.settled_at = utc_now()
    session.flush()
    return entry


def cohort_summary(
    session: Session,
    *,
    policy_version: str,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> dict[str, Any]:
    """Forward-cohort counts and outcomes. Never merged with backtests."""
    rows = list(
        session.scalars(
            select(ForwardLedgerEntry).where(
                ForwardLedgerEntry.policy_version == policy_version,
                ForwardLedgerEntry.data_mode == data_mode.value,
            )
        )
    )
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    settled = [r for r in rows if r.result in ("WIN", "LOSS", "PUSH")]
    pnl = [r.pnl_units or 0.0 for r in settled]
    clv_line = [r.clv_line for r in settled if r.clv_line is not None]
    clv_prob = [r.clv_probability for r in settled if r.clv_probability is not None]

    bankroll = peak = max_dd = 0.0
    for r in sorted(settled, key=lambda x: x.settled_at or x.created_at):
        bankroll += r.pnl_units or 0.0
        peak = max(peak, bankroll)
        max_dd = max(max_dd, peak - bankroll)

    return {
        "policy_version": policy_version,
        "data_mode": data_mode.value,
        "total_rows": len(rows),
        "statuses": statuses,
        "settled": len(settled),
        "wins": sum(1 for r in settled if r.result == "WIN"),
        "losses": sum(1 for r in settled if r.result == "LOSS"),
        "pushes": sum(1 for r in settled if r.result == "PUSH"),
        "total_pnl_units": sum(pnl),
        "roi_per_bet": (sum(pnl) / len(pnl)) if pnl else None,
        "max_drawdown_units": max_dd,
        "mean_clv_line": (sum(clv_line) / len(clv_line)) if clv_line else None,
        "mean_clv_probability": (sum(clv_prob) / len(clv_prob)) if clv_prob else None,
        "sample_warning": (
            "Sample is far too small for inference." if len(settled) < 100 else None
        ),
    }


def ledger_fingerprint(rows: list[ForwardLedgerEntry]) -> str:
    payload = "|".join(
        f"{r.canonical_game_id}:{r.market}:{r.selection}:{r.status}:{r.result}" for r in rows
    )
    return hashlib.sha256(payload.encode()).hexdigest()
