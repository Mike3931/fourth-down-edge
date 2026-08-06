"""Versioned consensus construction.

A consensus is a *derived* record computed from eligible raw quotes at an
instant. It is additive: raw quotes are never modified, replaced, or
deleted by it, so any consensus can be recomputed and audited against the
exact quotes that produced it (`quote_ids` carries the lineage).

Eligibility is deliberately strict. A quote that is live, stale,
duplicated, from an unrecognized book, or numerically impossible does not
enter the consensus, and the reason is countable.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.db.forward_models import ConsensusSnapshot, OddsQuote
from fde_api.forward.cohort import combine_provider_modes
from fde_api.forward.modes import DataMode
from fde_api.util import utc_now

CONSENSUS_METHOD_VERSION = "consensus-v1"

# Books accepted into consensus. An unrecognized book is excluded rather
# than trusted, because an unknown source cannot be quality-assessed.
RECOGNIZED_BOOKS = {
    "draftkings", "fanduel", "betmgm", "caesars", "pointsbetus", "betrivers",
    "williamhill_us", "bovada", "mybookieag", "betonlineag", "lowvig",
    "unibet_us", "superbook", "espnbet", "fanatics", "hardrockbet",
    "ballybet", "betparx", "windcreek", "novig", "prophetx",
}


def american_to_prob(american: int) -> float:
    return -american / (-american + 100.0) if american < 0 else 100.0 / (american + 100.0)


def no_vig_two_way(a: int, b: int) -> tuple[float, float]:
    """Remove vig from a two-way market by proportional normalization."""
    pa, pb = american_to_prob(a), american_to_prob(b)
    total = pa + pb
    if total <= 0:
        return 0.5, 0.5
    return pa / total, pb / total


@dataclass
class EligibilityReport:
    considered: int = 0
    eligible: int = 0
    rejected_live: int = 0
    rejected_stale: int = 0
    rejected_unknown_book: int = 0
    rejected_duplicate: int = 0
    rejected_invalid: int = 0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "eligible": self.eligible,
            "rejected_live": self.rejected_live,
            "rejected_stale": self.rejected_stale,
            "rejected_unknown_book": self.rejected_unknown_book,
            "rejected_duplicate": self.rejected_duplicate,
            "rejected_invalid": self.rejected_invalid,
        }


def select_eligible_quotes(
    quotes: list[OddsQuote],
    *,
    as_of_at: datetime,
    max_age_minutes: int,
    kickoff_utc: datetime | None,
) -> tuple[list[OddsQuote], EligibilityReport]:
    """Filter raw quotes to those admissible into a consensus at `as_of_at`.

    Point-in-time: a quote observed after the cutoff can never participate,
    so a consensus built for an earlier horizon cannot absorb later prices.
    """
    rep = EligibilityReport()
    cutoff_age = as_of_at - timedelta(minutes=max_age_minutes)
    seen: set[tuple[str, str, str, float | None, int]] = set()
    eligible: list[OddsQuote] = []

    for q in sorted(quotes, key=lambda x: (x.observed_at, x.id), reverse=True):
        rep.considered += 1
        if q.observed_at > as_of_at:
            rep.rejected_stale += 1  # future relative to the cutoff
            continue
        if q.is_live:
            rep.rejected_live += 1
            continue
        if kickoff_utc is not None and q.observed_at > kickoff_utc:
            rep.rejected_live += 1  # post-kickoff quote is in-play by definition
            continue
        if q.sportsbook not in RECOGNIZED_BOOKS:
            rep.rejected_unknown_book += 1
            continue
        if q.observed_at < cutoff_age:
            rep.rejected_stale += 1
            continue
        if abs(q.american) < 100 or (q.line is not None and abs(q.line) > 100):
            rep.rejected_invalid += 1
            continue
        # One quote per book/market/selection — newest wins (list is desc).
        key = (q.sportsbook, q.market, q.selection, None, 0)
        if key in seen:
            rep.rejected_duplicate += 1
            continue
        seen.add(key)
        eligible.append(q)

    rep.eligible = len(eligible)
    return eligible, rep


def build_consensus(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    as_of_at: datetime,
    kickoff_utc: datetime | None,
    max_age_minutes: int = 60,
    min_books: int = 3,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> tuple[ConsensusSnapshot | None, EligibilityReport]:
    """Compute and persist one consensus snapshot.

    Returns (None, report) when too few eligible books exist — the caller
    must then treat the market as DATA INCOMPLETE rather than guessing.
    """
    quotes = list(
        session.scalars(
            select(OddsQuote).where(
                OddsQuote.canonical_game_id == canonical_game_id,
                OddsQuote.market == market,
                OddsQuote.data_mode == data_mode.value,
            )
        )
    )
    eligible, rep = select_eligible_quotes(
        quotes, as_of_at=as_of_at, max_age_minutes=max_age_minutes, kickoff_utc=kickoff_utc
    )
    books = {q.sportsbook for q in eligible}
    if len(books) < min_books:
        rep.reasons.append(f"only {len(books)} eligible book(s), minimum is {min_books}")
        return None, rep

    def side(sel: str) -> list[OddsQuote]:
        return [q for q in eligible if q.selection == sel]

    median_line: float | None = None
    home_price = away_price = over_price = under_price = None
    no_vig_home = no_vig_over = None
    line_disp = price_disp = None

    if market == "SPREAD":
        home_q, away_q = side("HOME"), side("AWAY")
        # A spread is stored as the provider gives it: -3 on the favourite
        # and +3 on the underdog, one row each. Taking the median across
        # BOTH rows measured the sign convention rather than the market -
        # three books all posting home -3 yielded a median of 0.0 and a
        # dispersion of 3.0, reading as total disagreement between books
        # that agreed exactly. For any balanced two-sided market it
        # returned approximately zero, always, and `median_line` is what
        # CLV is measured against.
        #
        # So: one line per book, all from the HOME side. Folding the away
        # mirrors back in would double-count every book.
        home_lines = [q.line for q in home_q if q.line is not None]
        if not home_lines or not home_q or not away_q:
            rep.reasons.append("spread requires both sides and a line")
            return None, rep
        median_line = float(statistics.median(home_lines))
        # Price the consensus at the median line only — averaging prices
        # across different lines would invent a quote nobody offered.
        # The away side is matched at its own mirror of that line.
        at_line_home = [q for q in home_q if q.line == median_line] or home_q
        at_line_away = [q for q in away_q if q.line == -median_line] or away_q
        home_price = int(statistics.median([q.american for q in at_line_home]))
        away_price = int(statistics.median([q.american for q in at_line_away]))
        no_vig_home, _ = no_vig_two_way(home_price, away_price)
        line_disp = float(statistics.pstdev(home_lines)) if len(home_lines) > 1 else 0.0
        prices = [q.american for q in at_line_home]
        price_disp = float(statistics.pstdev(prices)) if len(prices) > 1 else 0.0

    elif market == "TOTAL":
        over_q, under_q = side("OVER"), side("UNDER")
        lines = [q.line for q in eligible if q.line is not None]
        if not lines or not over_q or not under_q:
            rep.reasons.append("total requires both sides and a line")
            return None, rep
        median_line = float(statistics.median(lines))
        at_line_over = [q for q in over_q if q.line == median_line] or over_q
        at_line_under = [q for q in under_q if q.line == median_line] or under_q
        over_price = int(statistics.median([q.american for q in at_line_over]))
        under_price = int(statistics.median([q.american for q in at_line_under]))
        no_vig_over, _ = no_vig_two_way(over_price, under_price)
        line_disp = float(statistics.pstdev(lines)) if len(lines) > 1 else 0.0
        prices = [q.american for q in at_line_over]
        price_disp = float(statistics.pstdev(prices)) if len(prices) > 1 else 0.0

    elif market == "MONEYLINE":
        home_q, away_q = side("HOME"), side("AWAY")
        if not home_q or not away_q:
            rep.reasons.append("moneyline requires both sides")
            return None, rep
        home_price = int(statistics.median([q.american for q in home_q]))
        away_price = int(statistics.median([q.american for q in away_q]))
        no_vig_home, _ = no_vig_two_way(home_price, away_price)
        prices = [q.american for q in home_q]
        price_disp = float(statistics.pstdev(prices)) if len(prices) > 1 else 0.0
    else:
        raise ValueError(f"Unsupported market {market!r}")

    ages = [int((as_of_at - q.observed_at).total_seconds()) for q in eligible]
    snap = ConsensusSnapshot(
        data_mode=data_mode.value,
        canonical_game_id=canonical_game_id,
        market=market,
        method_version=CONSENSUS_METHOD_VERSION,
        # Derived, never supplied: a consensus is only as live as its
        # least-live constituent quote.
        provider_mode=combine_provider_modes(q.provider_mode for q in eligible).value,
        median_line=median_line,
        home_price_american=home_price,
        away_price_american=away_price,
        over_price_american=over_price,
        under_price_american=under_price,
        no_vig_home_prob=no_vig_home,
        no_vig_over_prob=no_vig_over,
        eligible_books=len(books),
        line_dispersion=line_disp,
        price_dispersion=price_disp,
        oldest_quote_age_seconds=max(ages) if ages else None,
        newest_quote_age_seconds=min(ages) if ages else None,
        quote_ids={"quote_ids": [q.id for q in eligible], "books": sorted(books)},
        # Never set. The close is its own record now; a snapshot is an
        # observation and marking one would edit it. The column remains
        # so rows written before closing_captures existed stay readable.
        observed_at=as_of_at,
    )
    # The DATABASE decides, not this pre-check.
    #
    # This used to be SELECT -> not found -> INSERT, which is race-prone by
    # construction: two callers both see nothing and both insert. It passed
    # because SQLite serialises writers at the file level while PostgreSQL
    # does not. `upsert_by_identity` attempts the insert inside a savepoint
    # and treats the unique violation as the signal that another caller
    # established the slot first.
    #
    # Same slot + same content is a retry. Same slot + DIFFERENT content is
    # a contradiction about a value downstream reads as truth, so it is
    # returned as a conflict rather than quietly appended.
    from fde_api.forward.domain_identity import (
        CONSENSUS,
        IdentityResult,
        dispatch,
        report_conflict,
        upsert_by_identity,
    )

    logical = {
        "canonical_game_id": canonical_game_id,
        "market": market,
        "cutoff": as_of_at,
        "cohort": data_mode.value,
        "method_version": CONSENSUS_METHOD_VERSION,
    }
    content = {
        "median_line": snap.median_line,
        "home_price_american": snap.home_price_american,
        "away_price_american": snap.away_price_american,
        "over_price_american": snap.over_price_american,
        "under_price_american": snap.under_price_american,
        "no_vig_home_prob": snap.no_vig_home_prob,
        "no_vig_over_prob": snap.no_vig_over_prob,
        "eligible_books": snap.eligible_books,
        "quote_lineage": snap.quote_ids,
        "line_dispersion": snap.line_dispersion,
        "price_dispersion": snap.price_dispersion,
        "provider_mode": snap.provider_mode,
    }
    result = upsert_by_identity(
        session, ConsensusSnapshot, identity=CONSENSUS,
        logical_values=logical, content_values=content, build=lambda: snap,
    )
    # Total dispatch: all three branches are required keyword arguments, so
    # a fourth outcome - or a refactor that drops one - fails at the call
    # site instead of quietly taking whichever branch is left.
    def _note(text: str) -> None:
        rep.reasons.append(text)

    def _on_conflict(r: IdentityResult) -> None:
        _note(f"identity outcome: {r.outcome.value}")
        _note(
            "a DIFFERENT consensus already exists for this slot; the original "
            "stands and this requires review"
        )
        report_conflict(r, entity="consensus_snapshot")

    dispatch(
        result,
        created=lambda r: _note(f"identity outcome: {r.outcome.value}"),
        existing_identical=lambda r: _note(f"identity outcome: {r.outcome.value}"),
        conflict=lambda r: _on_conflict(r),
    )
    return result.record, rep


def latest_consensus_at(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    as_of_at: datetime,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
    exclude_closing_capture: bool = True,
) -> ConsensusSnapshot | None:
    """Newest consensus usable at a cutoff.

    Closing captures are excluded by default: they exist only for
    evaluation and must never feed an earlier prediction.
    """
    stmt = select(ConsensusSnapshot).where(
        ConsensusSnapshot.canonical_game_id == canonical_game_id,
        ConsensusSnapshot.market == market,
        ConsensusSnapshot.data_mode == data_mode.value,
        ConsensusSnapshot.observed_at <= as_of_at,
    )
    if exclude_closing_capture:
        stmt = stmt.where(ConsensusSnapshot.is_closing_capture.is_(False))
    return session.scalars(
        stmt.order_by(ConsensusSnapshot.observed_at.desc(), ConsensusSnapshot.id.desc()).limit(1)
    ).first()


def closing_consensus(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    kickoff_utc: datetime,
    max_age_before_kickoff_minutes: int = 30,
    data_mode: DataMode = DataMode.LIVE_RESEARCH,
) -> ConsensusSnapshot | None:
    """The closing snapshot, selected BY RULE: the last eligible consensus
    at or before kickoff and within the policy window.

    Deliberately not "the best-looking quote" — the rule is fixed in the
    forward-test policy before capture begins.
    """
    window_start = kickoff_utc - timedelta(minutes=max_age_before_kickoff_minutes)
    return session.scalars(
        select(ConsensusSnapshot)
        .where(
            ConsensusSnapshot.canonical_game_id == canonical_game_id,
            ConsensusSnapshot.market == market,
            ConsensusSnapshot.data_mode == data_mode.value,
            ConsensusSnapshot.observed_at <= kickoff_utc,
            ConsensusSnapshot.observed_at >= window_start,
        )
        .order_by(ConsensusSnapshot.observed_at.desc(), ConsensusSnapshot.id.desc())
        .limit(1)
    ).first()


def build_all_consensus_for_game(
    session: Session,
    *,
    canonical_game_id: str,
    kickoff_utc: datetime | None,
    as_of_at: datetime | None = None,
    markets: tuple[str, ...] = ("SPREAD", "TOTAL", "MONEYLINE"),
    **kwargs: Any,
) -> dict[str, Any]:
    as_of_at = as_of_at or utc_now()
    out: dict[str, Any] = {}
    for m in markets:
        snap, rep = build_consensus(
            session,
            canonical_game_id=canonical_game_id,
            market=m,
            as_of_at=as_of_at,
            kickoff_utc=kickoff_utc,
            **kwargs,
        )
        out[m] = {"snapshot_id": snap.id if snap else None, "eligibility": rep.as_dict(), "reasons": rep.reasons}
    return out
