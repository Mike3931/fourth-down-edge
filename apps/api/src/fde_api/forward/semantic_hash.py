"""A hash of what the chain MEANS, not of how it was stored.

The previous chain hash covered stage counts. That detects a missing stage
and nothing else: swap a line, flip a probability, settle the other way,
and the hash is unchanged. It could not answer the question the parity gate
actually asks - do two independently-run chains agree about the analysis?

This hashes the domain content: lines, prices, probabilities, expected
values, candidate states, cutoffs, observation times, governance versions,
lineage digests, fills, the selected close, settlement, P&L, CLV.

What it deliberately EXCLUDES is as important:

  * Autoincrement primary keys. Two databases assign different integers to
    the same fact; hashing them would guarantee the parity gate never
    passes and teach everyone to ignore it.
  * Scheduler run ids. The direct chain has none, by design.
  * Generation timestamps with no domain meaning - `created_at`, `entered_at`.
    When a row was WRITTEN is not what it says.
  * Insertion order. Records are sorted by a stable domain key before
    hashing, so two chains that produced the same facts in a different
    order agree.

Relationships are carried as the SEMANTIC identity of the upstream record,
never as its primary key: a prediction points at "the T-24h vintage for
this game under this policy", not at row 47.

The canonicalisation version is published with every digest. A change to
what is included or how it is rendered changes every hash, and a digest
without its version is a number nobody can reproduce later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
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
    ScheduleObservation,
    WeatherForecastVintage,
)

# Bump when the FIELD SET or the rendering changes. Every stored digest is
# meaningless without it, so it travels with the hash everywhere.
CANONICALIZATION_VERSION = "chain-semantic-v1"

# Floats are rounded before hashing. Two paths can compute the same
# probability through different arithmetic and differ in the last bits;
# that is not a disagreement about the analysis. Six places is far finer
# than any decision this system makes and far coarser than float noise.
_PRECISION = 6


def _num(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return round(float(value), _PRECISION)


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # A naive timestamp is a defect elsewhere; rendering it distinctly
        # means the hash exposes it rather than silently absorbing it.
        return f"NAIVE:{value.isoformat()}"
    # Normalised to UTC so two backends that return the same instant with
    # different tzinfo objects agree. The instant is the fact.
    return value.astimezone(UTC).isoformat()


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SemanticChain:
    """The domain content of one game's chain, ready to hash."""

    canonical_game_id: str
    cohort: str
    version: str
    records: list[dict[str, Any]]

    @property
    def digest(self) -> str:
        return _digest({
            "version": self.version,
            "canonical_game_id": self.canonical_game_id,
            "cohort": self.cohort,
            "records": self.records,
        })

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "canonical_game_id": self.canonical_game_id,
            "cohort": self.cohort,
            "record_count": len(self.records),
            "digest": self.digest,
        }

    def by_type(self, record_type: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r["type"] == record_type]


def _sorted(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order by the record's own semantic identity.

    Insertion order is a property of how the chain ran, not of what it
    concluded, so two chains that produced the same facts in a different
    sequence must agree.
    """
    return sorted(records, key=lambda r: _digest(r))


def build_semantic_chain(
    session: Session,
    *,
    canonical_game_id: str,
    data_mode: str,
    cohort: str,
    policy_version: str | None = None,
) -> SemanticChain:
    """Extract the domain-significant content of one game's chain."""
    g, dm = canonical_game_id, data_mode
    out: list[dict[str, Any]] = []

    for o in session.scalars(select(ScheduleObservation).where(
        ScheduleObservation.canonical_game_id == g, ScheduleObservation.data_mode == dm,
    )):
        out.append({
            "type": "schedule_observation",
            "identity": f"{g}|{o.observed_at.isoformat()}|{o.game_status}",
            "game_status": o.game_status,
            "kickoff_utc": _utc(o.kickoff_utc),
            "home_team_id": o.home_team_id,
            "away_team_id": o.away_team_id,
            "neutral_site": o.neutral_site,
            "international": o.international,
            "stadium_id": o.stadium_id,
            "observed_at": _utc(o.observed_at),
            "content_hash": o.content_hash,
            "change_summary": o.change_summary,
            "provider": o.provider,
        })

    for q in session.scalars(select(OddsQuote).where(
        OddsQuote.canonical_game_id == g, OddsQuote.data_mode == dm,
    )):
        out.append({
            "type": "odds_quote",
            "identity": f"{g}|{q.sportsbook}|{q.market}|{q.selection}|"
                        f"{q.observed_at.isoformat()}",
            "market": q.market,
            "selection": q.selection,
            "sportsbook": q.sportsbook,
            "line": _num(q.line),
            "american": q.american,
            "decimal_odds": _num(q.decimal_odds),
            "observed_at": _utc(q.observed_at),
            "provider_mode": q.provider_mode,
            "is_live": q.is_live,
            "raw_hash": q.raw_hash,
        })

    for c in session.scalars(select(ConsensusSnapshot).where(
        ConsensusSnapshot.canonical_game_id == g, ConsensusSnapshot.data_mode == dm,
    )):
        out.append({
            "type": "consensus_snapshot",
            "identity": f"{g}|{c.market}|{c.observed_at.isoformat()}",
            "market": c.market,
            "method_version": c.method_version,
            "median_line": _num(c.median_line),
            "home_price_american": c.home_price_american,
            "away_price_american": c.away_price_american,
            "over_price_american": c.over_price_american,
            "under_price_american": c.under_price_american,
            "no_vig_home_prob": _num(c.no_vig_home_prob),
            "no_vig_over_prob": _num(c.no_vig_over_prob),
            "eligible_books": c.eligible_books,
            "observed_at": _utc(c.observed_at),
            "provider_mode": c.provider_mode,
            # The lineage DIGEST, not the raw ids: two databases assign
            # different quote ids to the same quotes.
            "quote_lineage_digest": _digest(sorted(
                str(x) for x in (c.quote_ids or {}).get("ids", [])
            )) if c.quote_ids else None,
        })

    for w in session.scalars(select(WeatherForecastVintage).where(
        WeatherForecastVintage.canonical_game_id == g,
        WeatherForecastVintage.data_mode == dm,
    )):
        out.append({
            "type": "weather_vintage",
            "identity": f"{g}|{w.observed_at.isoformat()}",
            "temp_f": _num(w.temp_f),
            "wind_mph": _num(w.wind_mph),
            "precip_probability": _num(w.precip_probability),
            "observed_at": _utc(w.observed_at),
            "raw_hash": w.raw_hash,
            "provider": w.provider,
        })

    for i in session.scalars(select(InjuryObservation).where(
        InjuryObservation.canonical_game_id == g, InjuryObservation.data_mode == dm,
    )):
        out.append({
            "type": "injury_observation",
            "identity": f"{g}|{i.player_id}|{i.observed_at.isoformat()}",
            "team_id": i.team_id,
            "player_id": i.player_id,
            "practice_status": i.practice_status,
            "game_designation": i.game_designation,
            "source_category": i.source_category,
            "observed_at": _utc(i.observed_at),
        })

    for a in session.scalars(select(AvailabilityAssessment).where(
        AvailabilityAssessment.canonical_game_id == g,
        AvailabilityAssessment.data_mode == dm,
    )):
        out.append({
            "type": "availability_assessment",
            "identity": f"{g}|{a.player_id}|{a.as_of_at.isoformat()}",
            "player_id": a.player_id,
            "team_id": a.team_id,
            "state": a.state,
            "active_prob_low": _num(a.active_prob_low),
            "active_prob_high": _num(a.active_prob_high),
            "is_starting_qb": a.is_starting_qb,
            "confidence_tier": a.confidence_tier,
            "as_of_at": _utc(a.as_of_at),
        })

    preds = list(session.scalars(select(ForwardPrediction).where(
        ForwardPrediction.canonical_game_id == g, ForwardPrediction.data_mode == dm,
    )))
    # Semantic identity of a prediction, used by ledger rows to point at it
    # without borrowing its primary key.
    pred_identity: dict[str, str] = {}
    for p in preds:
        identity = f"{g}|{p.horizon}|{p.as_of_at.isoformat()}|{p.policy_version}"
        pred_identity[p.id] = identity
        out.append({
            "type": "prediction_vintage",
            "identity": identity,
            "horizon": p.horizon,
            "as_of_at": _utc(p.as_of_at),
            "expected_margin": _num(p.expected_margin),
            "expected_total": _num(p.expected_total),
            "home_win_prob": _num(p.home_win_prob),
            "spread_cover_prob": _num(p.spread_cover_prob),
            "spread_push_prob": _num(p.spread_push_prob),
            "total_over_prob": _num(p.total_over_prob),
            "total_push_prob": _num(p.total_push_prob),
            "margin_p10": _num(p.margin_p10),
            "margin_p90": _num(p.margin_p90),
            "total_p10": _num(p.total_p10),
            "total_p90": _num(p.total_p90),
            "data_completeness": _num(p.data_completeness),
            "policy_version": p.policy_version,
            "model_version": p.model_version,
            "feature_set_version": p.feature_set_version,
            "calibration_version": p.calibration_version,
            "artifact_hash": p.artifact_hash,
            "lineage_digest": _digest(p.lineage) if p.lineage else None,
            "warnings": sorted(p.warnings or []) if p.warnings else None,
        })

    for m in session.scalars(select(ManualBookPriceEntry).where(
        ManualBookPriceEntry.canonical_game_id == g,
        ManualBookPriceEntry.data_mode == dm,
    )):
        out.append({
            "type": "price_observation",
            "identity": f"{g}|{m.market}|{m.selection}|{m.observed_at.isoformat()}",
            "market": m.market,
            "selection": m.selection,
            "line": _num(m.line),
            "american": m.american,
            "decimal_odds": _num(m.decimal_odds),
            "break_even_probability": _num(m.break_even_probability),
            "observed_at": _utc(m.observed_at),
            "confirmed": m.confirmed_at is not None,
            "source": m.source,
            "cohort": m.cohort,
            "provider_mode": m.provider_mode,
            "policy_version": m.policy_version,
            "superseded": m.superseded_by_id is not None,
        })

    # Closing captures. The referenced snapshot is carried by its OBSERVED
    # INSTANT and market, not by its row id.
    snapshots = {c.id: c for c in session.scalars(select(ConsensusSnapshot).where(
        ConsensusSnapshot.canonical_game_id == g, ConsensusSnapshot.data_mode == dm,
    ))}
    close_identity: dict[str, str] = {}
    for cc in session.scalars(select(ClosingCapture).where(
        ClosingCapture.canonical_game_id == g, ClosingCapture.data_mode == dm,
    )):
        ref = snapshots.get(cc.consensus_snapshot_id) if cc.consensus_snapshot_id else None
        identity = (
            f"{g}|{cc.market}|{cc.selection or '-'}|{cc.cohort}|"
            f"{cc.selection_rule_version}|{cc.status}|"
            f"{ref.observed_at.isoformat() if ref else 'none'}"
        )
        close_identity[cc.id] = identity
        out.append({
            "type": "closing_capture",
            "identity": identity,
            "market": cc.market,
            "selection": cc.selection,
            "status": cc.status,
            "selection_rule": cc.selection_rule,
            "selection_rule_version": cc.selection_rule_version,
            "referenced_snapshot_observed_at": _utc(ref.observed_at) if ref else None,
            "referenced_snapshot_line": _num(ref.median_line) if ref else None,
            "missing_close_reason": cc.missing_close_reason,
            "conflict_reason": cc.conflict_reason,
            "cohort": cc.cohort,
            "provider_mode": cc.provider_mode,
            "policy_version": cc.policy_version,
        })

    ledger_stmt = select(ForwardLedgerEntry).where(
        ForwardLedgerEntry.canonical_game_id == g, ForwardLedgerEntry.data_mode == dm,
    )
    if policy_version:
        ledger_stmt = ledger_stmt.where(ForwardLedgerEntry.policy_version == policy_version)
    for e in session.scalars(ledger_stmt):
        out.append({
            "type": "forward_performance",
            "identity": f"{g}|{e.market}|{e.selection}|{e.horizon}|"
                        f"{e.as_of_at.isoformat()}|{e.policy_version}",
            "market": e.market,
            "selection": e.selection,
            "horizon": e.horizon,
            "status": e.status,
            "as_of_at": _utc(e.as_of_at),
            "qualifying_line": _num(e.qualifying_line),
            "qualifying_american": e.qualifying_american,
            "price_source": e.price_source,
            "model_probability": _num(e.model_probability),
            "conservative_probability": _num(e.conservative_probability),
            "break_even_probability": _num(e.break_even_probability),
            "expected_value": _num(e.expected_value),
            "filled": e.filled,
            "fill_note": e.fill_note,
            "simulated_line": _num(e.simulated_line),
            "simulated_american": e.simulated_american,
            "simulated_delay_seconds": e.simulated_delay_seconds,
            "result": e.result,
            "pnl_units": _num(e.pnl_units),
            "closing_line": _num(e.closing_line),
            "closing_american": e.closing_american,
            "closing_no_vig_prob": _num(e.closing_no_vig_prob),
            "clv_line": _num(e.clv_line),
            "clv_probability": _num(e.clv_probability),
            "data_completeness": _num(e.data_completeness),
            "exclusion_reason": e.exclusion_reason,
            "policy_version": e.policy_version,
            "model_version": e.model_version,
            "settled": e.settled_at is not None,
            # The upstream vintage by its SEMANTIC identity, never its key.
            "prediction_identity": pred_identity.get(e.forward_prediction_id or ""),
            "reasons_digest": _digest(e.reasons) if e.reasons else None,
        })

    return SemanticChain(
        canonical_game_id=canonical_game_id,
        cohort=cohort,
        version=CANONICALIZATION_VERSION,
        records=_sorted(out),
    )


def compare(a: SemanticChain, b: SemanticChain) -> dict[str, Any]:
    """A readable diff of two chains.

    Reports the record identities present on one side only, and the fields
    that differ on shared identities. A bare "hashes differ" would send
    somebody hunting through two databases by hand.
    """
    def index(chain: SemanticChain) -> dict[tuple[str, str], dict[str, Any]]:
        return {(r["type"], r["identity"]): r for r in chain.records}

    def counts(chain: SemanticChain) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for r in chain.records:
            key = (r["type"], r["identity"])
            out[key] = out.get(key, 0) + 1
        return out

    ia, ib = index(a), index(b)
    ca, cb = counts(a), counts(b)
    # Duplicates were invisible here at first: indexing by identity
    # collapses them, so two chains that differed ONLY by a duplicated
    # record reported no differences and a mismatched digest - the worst
    # possible output, since it says something is wrong and nothing about
    # what. Multiplicity is part of the comparison.
    duplicated = sorted(
        f"{t}:{i} (a={ca.get((t, i), 0)}, b={cb.get((t, i), 0)})"
        for (t, i) in set(ca) | set(cb)
        if ca.get((t, i), 0) != cb.get((t, i), 0)
        or ca.get((t, i), 0) > 1
        or cb.get((t, i), 0) > 1
    )
    only_a = sorted(f"{t}:{i}" for (t, i) in ia.keys() - ib.keys())
    only_b = sorted(f"{t}:{i}" for (t, i) in ib.keys() - ia.keys())

    differing: list[dict[str, Any]] = []
    for key in sorted(ia.keys() & ib.keys()):
        ra, rb = ia[key], ib[key]
        fields = {
            k: {"a": ra.get(k), "b": rb.get(k)}
            for k in sorted(set(ra) | set(rb))
            if ra.get(k) != rb.get(k)
        }
        if fields:
            differing.append({"type": key[0], "identity": key[1], "fields": fields})

    return {
        "equal": a.digest == b.digest,
        "digest_a": a.digest,
        "digest_b": b.digest,
        "version": a.version,
        "only_in_a": only_a,
        "only_in_b": only_b,
        "duplicated_or_uneven_multiplicity": duplicated,
        "differing": differing,
    }
