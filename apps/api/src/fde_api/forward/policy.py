"""The forward-test policy — the rules of the experiment, frozen before
the experiment starts.

Every analytical decision that could otherwise be made *after* seeing
results is fixed here in advance: which model, which threshold, how
stale a price may be, how execution is simulated, which quote counts as
the close, what happens when data is missing, and which games are
excluded. The policy is content-hashed; changing any field yields a new
hash, a new version, and a new evaluation cohort.

This is what makes a forward test evidence rather than a story. If the
rules could move after the fact, positive results would be unfalsifiable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.config import settings
from fde_api.db.forward_models import ForwardTestPolicyRecord
from fde_api.util import current_code_commit, dependency_lock_hash, utc_now

POLICY_SCHEMA_VERSION = "forward-policy-v1"


class MarketSelectionRules(BaseModel):
    markets: tuple[str, ...] = ("SPREAD", "TOTAL", "MONEYLINE")
    # Only the consensus line is evaluated; alternate lines are out of scope
    # for v1 because no historical alternate-line data supports them.
    evaluate_alternate_lines: bool = False
    min_eligible_books: int = 3


class PriceStalenessRule(BaseModel):
    max_consensus_age_minutes: int = 60
    max_manual_price_age_minutes: int = 30
    require_reconfirm_within_minutes_of_kickoff: int = 90


class ExecutionSimulation(BaseModel):
    manual_entry_delay_seconds: int = 180
    price_deterioration_cents: int = 5
    halfpoint_deterioration_probability: float = 0.25
    no_fill_probability: float = 0.02
    suspended_probability: float = 0.005
    # Fills are simulated deterministically from (policy seed, game, market,
    # selection) so a rerun reproduces the same ledger exactly.
    seed: int = 20260801


class ClosingLineDefinition(BaseModel):
    """Which observation counts as 'the close' — chosen by rule, never by
    looking at which quote flatters the result."""

    rule: str = "last eligible consensus snapshot at or before kickoff"
    max_age_before_kickoff_minutes: int = 30
    fallback: str = "if no eligible snapshot within window, CLV is unavailable for that game"
    use_no_vig_probability: bool = True


class MissingDataPolicy(BaseModel):
    on_missing_consensus: Literal["DATA_INCOMPLETE"] = "DATA_INCOMPLETE"
    on_missing_starting_qb: Literal["DATA_INCOMPLETE", "WEIGHTED_SCENARIOS"] = "DATA_INCOMPLETE"
    on_unknown_roof_state_outdoor_venue: Literal["DATA_INCOMPLETE"] = "DATA_INCOMPLETE"
    on_stale_price: Literal["DATA_INCOMPLETE"] = "DATA_INCOMPLETE"
    min_data_completeness_for_candidate: float = 0.80


class SettlementPolicy(BaseModel):
    source: str = "nflverse final scores"
    push_handling: str = "stake returned, counted as a settled non-win"
    void_handling: str = "stake returned; excluded from ROI, retained in the ledger"
    settle_no_earlier_than_minutes_after_kickoff: int = 270


class ExclusionRules(BaseModel):
    exclude_preseason: bool = True
    exclude_postponed: bool = True
    exclude_cancelled: bool = True
    exclude_neutral_site_international: bool = False
    exclude_games_without_eligible_consensus: bool = True
    note: str = (
        "Exclusions are applied by rule at capture time and recorded on the ledger row; "
        "a game is never dropped retroactively because its result was unhelpful."
    )


class BankrollSimulation(BaseModel):
    starting_units: float = 100.0
    stake_per_candidate_units: float = 1.0
    sizing: str = "flat 1 unit per research candidate (no Kelly during forward test)"
    max_concurrent_units: float = 10.0
    stop_rule: str = "informational only; the forward test never stops on drawdown"


class ForwardTestPolicy(BaseModel):
    """Immutable once frozen. `policy_hash` covers every field below it."""

    schema_version: str = POLICY_SCHEMA_VERSION
    policy_version: str

    # Approved analytical components (frozen at phase-2-research-engine-v1).
    model_version: str
    feature_set_version: str
    calibration_version: str | None
    recommendation_rule_version: str

    prediction_horizons: tuple[str, ...]
    market_selection: MarketSelectionRules = Field(default_factory=MarketSelectionRules)
    research_candidate_edge_threshold: float
    watch_band_width: float
    price_staleness: PriceStalenessRule = Field(default_factory=PriceStalenessRule)
    execution: ExecutionSimulation = Field(default_factory=ExecutionSimulation)
    closing_line: ClosingLineDefinition = Field(default_factory=ClosingLineDefinition)
    missing_data: MissingDataPolicy = Field(default_factory=MissingDataPolicy)
    settlement: SettlementPolicy = Field(default_factory=SettlementPolicy)
    exclusions: ExclusionRules = Field(default_factory=ExclusionRules)
    bankroll: BankrollSimulation = Field(default_factory=BankrollSimulation)

    start_date: date
    end_date: date

    code_commit: str
    dependency_lock_hash: str

    def canonical_payload(self) -> str:
        """Deterministic serialization used for hashing (hash field excluded)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def policy_hash(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode()).hexdigest()


def build_policy_draft(
    *,
    policy_version: str,
    model_version: str = "market-residual-v1",
    feature_set_version: str = "nfl-core-v1",
    calibration_version: str | None = None,
    start: date,
    end: date,
) -> ForwardTestPolicy:
    """Build a DRAFT policy from current code.

    Uses the *current* code commit, so a draft built later differs from an
    already-frozen policy. That is correct and expected: the frozen record
    keeps the commit it was frozen at, and verification never rebuilds a
    draft. Only `freeze_policy` may create an immutable record.

    Threshold and band are carried over unchanged from the frozen Phase 2
    configuration. They are deliberately NOT re-tuned: the 2024/2025
    seasons have been inspected (see docs/model-governance.md), so tuning
    against them now would contaminate the forward test before it starts.
    """
    return ForwardTestPolicy(
        policy_version=policy_version,
        model_version=model_version,
        feature_set_version=feature_set_version,
        calibration_version=calibration_version,
        recommendation_rule_version="research-candidate-rules-v1",
        prediction_horizons=(
            "OPENING",
            "EARLY_WEEK",
            "PRACTICE_UPDATE",
            "FINAL_INJURY_REPORT",
            "PREGAME",
            "CLOSING_CAPTURE_EVALUATION_ONLY",
        ),
        research_candidate_edge_threshold=0.05,
        watch_band_width=0.015,
        start_date=start,
        end_date=end,
        code_commit=current_code_commit(),
        dependency_lock_hash=dependency_lock_hash(),
    )


class PolicyImmutabilityError(RuntimeError):
    """Raised on any attempt to alter a frozen policy in place."""


def freeze_policy(session: Session, policy: ForwardTestPolicy) -> ForwardTestPolicyRecord:
    """Persist a policy immutably.

    Re-freezing an identical policy is a no-op that returns the existing
    record. Freezing a *different* policy under an existing version is
    refused — a changed rule must take a new version, which starts a new
    evaluation cohort.
    """
    phash = policy.policy_hash()
    existing = session.get(ForwardTestPolicyRecord, policy.policy_version)
    if existing is not None:
        if existing.policy_hash != phash:
            raise PolicyImmutabilityError(
                f"Policy version {policy.policy_version} is already frozen with hash "
                f"{existing.policy_hash[:12]}; refusing to modify it. Changing any rule "
                "requires a NEW policy version and starts a separate evaluation cohort."
            )
        return existing

    settings.ensure_dirs()
    policy_dir = settings.data_dir / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    path = policy_dir / f"{policy.policy_version}_{phash[:12]}.json"
    if not path.exists():
        path.write_text(json.dumps(policy.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")

    record = ForwardTestPolicyRecord(
        policy_version=policy.policy_version,
        policy_hash=phash,
        schema_version=policy.schema_version,
        payload=policy.model_dump(mode="json"),
        model_version=policy.model_version,
        feature_set_version=policy.feature_set_version,
        calibration_version=policy.calibration_version,
        start_date=policy.start_date.isoformat(),
        end_date=policy.end_date.isoformat(),
        code_commit=policy.code_commit,
        dependency_lock_hash=policy.dependency_lock_hash,
        artifact_path=str(path),
        created_at=utc_now(),
    )
    session.add(record)
    session.flush()
    return record


def active_policy(session: Session, at: date | None = None) -> ForwardTestPolicy | None:
    """The policy in force on a date (default: today). Returns None when no
    policy covers it — callers must then refuse to generate candidates."""
    at = at or utc_now().date()
    for rec in session.scalars(
        select(ForwardTestPolicyRecord).order_by(ForwardTestPolicyRecord.created_at.desc())
    ):
        if rec.start_date <= at.isoformat() <= rec.end_date:
            return ForwardTestPolicy.model_validate(rec.payload)
    return None


def load_frozen_policy(session: Session, policy_version: str) -> ForwardTestPolicy:
    """Read a frozen policy from its stored payload and verify it.

    Never rebuilds a draft: the stored payload is the authority, including
    the commit it was frozen at.
    """
    rec = session.get(ForwardTestPolicyRecord, policy_version)
    if rec is None:
        raise LookupError(f"Unknown forward-test policy version {policy_version}")
    if not verify_frozen_policy(session, policy_version):
        raise PolicyImmutabilityError(
            f"Stored policy {policy_version} fails hash verification — "
            "the immutable record has been tampered with."
        )
    return ForwardTestPolicy.model_validate(rec.payload)


def verify_frozen_policy(session: Session, policy_version: str) -> bool:
    """Hash the STORED canonical payload and compare to the stored hash.

    This is the whole point of the creation/verification split: it never
    consults current code, so shipping new application commits cannot
    change the verdict.
    """
    rec = session.get(ForwardTestPolicyRecord, policy_version)
    if rec is None:
        raise LookupError(f"Unknown forward-test policy version {policy_version}")
    recomputed = ForwardTestPolicy.model_validate(rec.payload).policy_hash()
    return recomputed == rec.policy_hash


# Backwards-compatible aliases.
load_policy = load_frozen_policy
build_default_policy = build_policy_draft


def verify_all_policies(session: Session) -> list[dict[str, Any]]:
    """Integrity sweep: recompute every stored policy's hash."""
    out = []
    for rec in session.scalars(select(ForwardTestPolicyRecord)):
        policy = ForwardTestPolicy.model_validate(rec.payload)
        recomputed = policy.policy_hash()
        out.append(
            {
                "policy_version": rec.policy_version,
                "recorded_hash": rec.policy_hash,
                "recomputed_hash": recomputed,
                "intact": recomputed == rec.policy_hash,
                "artifact_exists": Path(rec.artifact_path).exists() if rec.artifact_path else False,
            }
        )
    return out
