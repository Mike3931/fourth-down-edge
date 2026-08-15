"""Immutable forward prediction vintages.

A vintage is what the system believed at a defined cutoff, with the
lineage to prove it. Once written it is never modified: a later horizon
is a NEW row. Regenerating an existing vintage must reproduce identical
content, and a mismatch is raised rather than silently overwritten —
that check is what makes "reproducible" a testable claim instead of an
aspiration.

CLOSING_CAPTURE_EVALUATION_ONLY is stored like any other vintage but is
excluded from every prediction input path; it exists to measure CLV.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ForwardPrediction, ScheduleObservation, Venue
from fde_api.forward.cohort import Cohort
from fde_api.forward.consensus import latest_consensus_at
from fde_api.forward.injuries import availability_snapshot, resolve_starting_qb
from fde_api.forward.modes import DataMode
from fde_api.forward.policy import ForwardTestPolicy
from fde_api.forward.weather import forecast_as_of, roof_state_as_of, weather_snapshot_dict
from fde_api.models_ml.distribution import GameDistribution, MuSigma
from fde_api.util import utc_now

# Offsets before kickoff defining each horizon's cutoff.
HORIZON_OFFSETS: dict[str, timedelta] = {
    "OPENING": timedelta(days=6),
    "EARLY_WEEK": timedelta(days=4),
    "PRACTICE_UPDATE": timedelta(days=2),
    "FINAL_INJURY_REPORT": timedelta(days=1),
    "PREGAME": timedelta(minutes=90),
    "CLOSING_CAPTURE_EVALUATION_ONLY": timedelta(0),
}

PREDICTION_HORIZONS = tuple(h for h in HORIZON_OFFSETS if h != "CLOSING_CAPTURE_EVALUATION_ONLY")


class VintageImmutabilityError(RuntimeError):
    """A regenerated vintage differs from the stored one."""


def horizon_cutoff(kickoff_utc: datetime, horizon: str) -> datetime:
    try:
        return kickoff_utc - HORIZON_OFFSETS[horizon]
    except KeyError as e:
        raise ValueError(f"Unknown horizon {horizon!r}") from e


@dataclass
class VintageInputs:
    """Everything the model may see at a cutoff, plus why anything is missing."""

    consensus: dict[str, Any] = field(default_factory=dict)
    weather: dict[str, Any] | None = None
    roof_state: str = "UNKNOWN"
    availability: dict[str, Any] = field(default_factory=dict)
    qb: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    completeness: float = 0.0


def gather_inputs(
    session: Session,
    *,
    game: ScheduleObservation,
    horizon: str,
    as_of_at: datetime,
    policy: ForwardTestPolicy,
    cohort: Cohort,
    expected_home_qb: str | None = None,
    expected_away_qb: str | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> VintageInputs:
    """Collect point-in-time inputs and score data completeness.

    Every lookup is cutoff-bounded, so a later observation cannot reach
    an earlier vintage.
    """
    out = VintageInputs()
    weights_total = 0.0
    weights_have = 0.0

    # --- market (heaviest weight: no price, no evaluation) ---------------
    for market in policy.market_selection.markets:
        weights_total += 1.0
        snap = latest_consensus_at(
            session, canonical_game_id=game.canonical_game_id, market=market,
            as_of_at=as_of_at, cohort=cohort, data_mode=data_mode,
        )
        if snap is None:
            out.warnings.append(f"no eligible {market} consensus at cutoff")
            continue
        age_min = (as_of_at - snap.observed_at).total_seconds() / 60.0
        if age_min > policy.price_staleness.max_consensus_age_minutes:
            out.warnings.append(
                f"{market} consensus is {age_min:.0f}m old (limit "
                f"{policy.price_staleness.max_consensus_age_minutes}m)"
            )
            continue
        if snap.eligible_books < policy.market_selection.min_eligible_books:
            out.warnings.append(
                f"{market} consensus has {snap.eligible_books} books (min "
                f"{policy.market_selection.min_eligible_books})"
            )
            continue
        weights_have += 1.0
        out.consensus[market] = {
            "snapshot_id": snap.id,
            "median_line": snap.median_line,
            "home_price": snap.home_price_american,
            "away_price": snap.away_price_american,
            "over_price": snap.over_price_american,
            "under_price": snap.under_price_american,
            "no_vig_home_prob": snap.no_vig_home_prob,
            "no_vig_over_prob": snap.no_vig_over_prob,
            "eligible_books": snap.eligible_books,
            "age_seconds": int((as_of_at - snap.observed_at).total_seconds()),
            "observed_at": snap.observed_at.isoformat(),
        }

    # --- venue / weather -------------------------------------------------
    venue = session.get(Venue, game.stadium_id) if game.stadium_id else None
    out.roof_state = roof_state_as_of(
        session, canonical_game_id=game.canonical_game_id, as_of_at=as_of_at,
        venue=venue, data_mode=data_mode,
    )
    weather_needed = venue is not None and venue.weather_applicable and venue.country == "US"
    if weather_needed:
        weights_total += 1.0
        wx = forecast_as_of(
            session, canonical_game_id=game.canonical_game_id, as_of_at=as_of_at, data_mode=data_mode
        )
        out.weather = weather_snapshot_dict(wx)
        if wx is None:
            out.warnings.append("no weather forecast vintage at cutoff")
        else:
            weights_have += 1.0
    elif venue is None:
        out.warnings.append("venue not resolved; weather applicability unknown")
    elif venue.country != "US":
        out.warnings.append(f"international venue ({venue.country}); NWS forecast unavailable by design")

    if venue is not None and venue.roof_type == "RETRACTABLE" and out.roof_state == "UNKNOWN":
        out.warnings.append("retractable roof state unknown at cutoff")

    # --- availability ----------------------------------------------------
    weights_total += 1.0
    out.availability = availability_snapshot(
        session, canonical_game_id=game.canonical_game_id, as_of_at=as_of_at, data_mode=data_mode
    )
    if out.availability["verified_observations"] > 0:
        weights_have += 1.0
    else:
        out.warnings.append("no verified injury observations at cutoff")

    # --- starting quarterbacks -------------------------------------------
    weights_total += 1.0
    home_qb = resolve_starting_qb(
        session, canonical_game_id=game.canonical_game_id, team_id=game.home_team_id,
        expected_qb_id=expected_home_qb, as_of_at=as_of_at, data_mode=data_mode,
    )
    away_qb = resolve_starting_qb(
        session, canonical_game_id=game.canonical_game_id, team_id=game.away_team_id,
        expected_qb_id=expected_away_qb, as_of_at=as_of_at, data_mode=data_mode,
    )
    out.qb = {"home": home_qb.as_dict(), "away": away_qb.as_dict()}
    if home_qb.resolved and away_qb.resolved:
        weights_have += 1.0
    else:
        for side, r in (("home", home_qb), ("away", away_qb)):
            if not r.resolved:
                out.warnings.append(f"{side} starting QB unresolved: {r.reason}")

    out.completeness = round(weights_have / weights_total, 4) if weights_total else 0.0
    return out


def _artifact_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def generate_vintage(
    session: Session,
    *,
    game: ScheduleObservation,
    horizon: str,
    policy: ForwardTestPolicy,
    moments: tuple[float, float, float, float] | None,
    cohort: Cohort,
    expected_home_qb: str | None = None,
    expected_away_qb: str | None = None,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    now: datetime | None = None,
) -> tuple[ForwardPrediction | None, VintageInputs]:
    """Create (or verify) one immutable vintage.

    `moments` is (mu_margin, sigma_margin, mu_total, sigma_total) from the
    frozen model. Passing None records a DATA-INCOMPLETE vintage that
    carries its warnings but no probabilities — the honest representation
    of "we could not predict this".
    """
    now = now or utc_now()
    if game.kickoff_utc is None:
        raise ValueError(f"{game.canonical_game_id} has no kickoff; cannot build a vintage")
    as_of_at = horizon_cutoff(game.kickoff_utc, horizon)

    # A vintage may only be generated once its cutoff has actually arrived.
    if as_of_at > now:
        return None, VintageInputs(warnings=[f"cutoff {as_of_at.isoformat()} has not arrived yet"])

    inputs = gather_inputs(
        session, game=game, horizon=horizon, as_of_at=as_of_at, policy=policy,
        cohort=cohort, expected_home_qb=expected_home_qb,
        expected_away_qb=expected_away_qb, data_mode=data_mode,
    )

    spread = inputs.consensus.get("SPREAD", {})
    total = inputs.consensus.get("TOTAL", {})
    home_line = spread.get("median_line")
    total_line = total.get("median_line")

    outputs: dict[str, Any] = {}
    if moments is not None:
        mu_m, sd_m, mu_t, sd_t = moments
        dist = GameDistribution(MuSigma(mu_m, sd_m), MuSigma(mu_t, sd_t))
        outputs = dist.outputs(home_line, total_line)

    payload = {
        "game": game.canonical_game_id,
        "horizon": horizon,
        "as_of_at": as_of_at.isoformat(),
        "model_version": policy.model_version,
        "feature_set": policy.feature_set_version,
        "calibration": policy.calibration_version,
        "policy_version": policy.policy_version,
        "consensus": inputs.consensus,
        "weather": inputs.weather,
        "roof_state": inputs.roof_state,
        "availability": inputs.availability,
        "qb": inputs.qb,
        "outputs": outputs,
    }
    artifact_hash = _artifact_hash(payload)

    pred_id = f"fp_{game.canonical_game_id}_{horizon}_{policy.policy_version}"
    existing = session.get(ForwardPrediction, pred_id)
    if existing is not None:
        if existing.artifact_hash != artifact_hash:
            raise VintageImmutabilityError(
                f"Vintage {pred_id} already exists with hash {existing.artifact_hash[:12]} but "
                f"regenerated to {artifact_hash[:12]}. Vintages are immutable; a changed input "
                "means a new horizon or a new policy version, never an overwrite."
            )
        return existing, inputs

    lineage = {
        "consensus_snapshot_ids": {m: v.get("snapshot_id") for m, v in inputs.consensus.items()},
        "weather_vintage_id": (inputs.weather or {}).get("vintage_id"),
        "injury_observation_ids": inputs.availability.get("observation_ids", []),
        "schedule_observation_id": game.id,
        "roof_state": inputs.roof_state,
        "qb_resolution": inputs.qb,
    }

    pred = ForwardPrediction(
        id=pred_id,
        data_mode=data_mode.value,
        canonical_game_id=game.canonical_game_id,
        horizon=horizon,
        as_of_at=as_of_at,
        model_version=policy.model_version,
        calibration_version=policy.calibration_version,
        feature_set_version=policy.feature_set_version,
        policy_version=policy.policy_version,
        lineage=lineage,
        consensus_snapshot_id=(spread.get("snapshot_id") or total.get("snapshot_id")),
        weather_vintage_id=(inputs.weather or {}).get("vintage_id"),
        availability_snapshot=inputs.availability,
        expected_margin=outputs.get("expected_margin"),
        expected_total=outputs.get("expected_total"),
        home_win_prob=outputs.get("home_win_prob"),
        spread_cover_prob=outputs.get("spread_cover_prob"),
        spread_push_prob=outputs.get("spread_push_prob"),
        total_over_prob=outputs.get("total_over_prob"),
        total_push_prob=outputs.get("total_push_prob"),
        margin_p10=outputs.get("margin_p10"),
        margin_p90=outputs.get("margin_p90"),
        total_p10=outputs.get("total_p10"),
        total_p90=outputs.get("total_p90"),
        data_completeness=inputs.completeness,
        warnings={"warnings": inputs.warnings},
        artifact_hash=artifact_hash,
        created_at=now,
    )
    session.add(pred)
    session.flush()
    return pred, inputs


def vintages_for_game(
    session: Session,
    canonical_game_id: str,
    *,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> list[ForwardPrediction]:
    return list(
        session.scalars(
            select(ForwardPrediction)
            .where(
                ForwardPrediction.canonical_game_id == canonical_game_id,
                ForwardPrediction.data_mode == data_mode.value,
            )
            .order_by(ForwardPrediction.as_of_at)
        )
    )
