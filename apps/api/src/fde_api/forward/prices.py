"""Manually observed prices — the one record with no provider behind it.

Every other market record in the chain came from a provider that can be
asked again. A price the user read off a screen and typed in cannot be
re-fetched, re-verified, or corrected by refetching. That asymmetry drives
every decision here:

  * **The record is immutable.** A correction does not edit the row; it
    writes a new row that points at the one it replaces, and the original
    is marked superseded. The history of what was believed, and when,
    survives.
  * **Two distinct timestamps.** `observed_at` is when the USER saw the
    price; `entered_at` is when it reached the database. They are not the
    same instant and conflating them would misstate price age, which is an
    input to every downstream evaluation.
  * **Confirmation is a state, not a flag on the way in.** An unconfirmed
    entry is a claim; a confirmed one has been re-read by the person who
    entered it. Downstream consumers can require confirmation.
  * **Derived values are stored.** Decimal odds and break-even probability
    are computed once, at entry, and persisted. If the conversion ever
    changes, historical rows keep the arithmetic they were evaluated under
    rather than silently acquiring today's.

The software never retrieves, scrapes, inspects, refreshes, automates, or
communicates with bet365. Nothing in this module opens a network
connection. The price arrives as an argument because a human typed it.

A stored price is a historical observation. Nothing here states or implies
that the price is still available.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fde_api.backtest.execution import break_even_prob
from fde_api.db.forward_models import ManualBookPriceEntry
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode
from fde_api.forward.ordering import newest
from fde_api.util import current_code_commit, utc_now

# Markets and selections a price may be entered against. Free text here
# would let a typo create a price nothing downstream can match to a market.
VALID_MARKETS = ("SPREAD", "TOTAL", "MONEYLINE")
VALID_SELECTIONS = {
    "SPREAD": ("HOME", "AWAY"),
    "TOTAL": ("OVER", "UNDER"),
    "MONEYLINE": ("HOME", "AWAY"),
}

# A price entered as coming from a live sportsbook must not be recorded
# under a fixture provider mode, and vice versa. The source names the book;
# the provider mode names how the value reached us.
SOURCE_MANUAL = "manual_bet365"
SOURCE_FIXTURE = "fixture_price"


class PriceEntryError(ValueError):
    """A price entry that would be unusable or misleading if stored."""


def american_to_decimal(american: int) -> float:
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


@dataclass(frozen=True)
class PriceObservation:
    """What a caller supplies. Validated before anything is written."""

    canonical_game_id: str
    market: str
    selection: str
    american: int
    observed_at: datetime
    user_id: str
    line: float | None = None
    source: str = SOURCE_FIXTURE
    cohort: Cohort = Cohort.FIXTURE
    provider_mode: ProviderMode = ProviderMode.FIXTURE
    policy_version: str | None = None
    data_mode: DataMode = DataMode.DEMO
    confirmed: bool = False


def _validate(obs: PriceObservation, *, now: datetime) -> None:
    if obs.market not in VALID_MARKETS:
        raise PriceEntryError(f"unknown market {obs.market!r}; expected one of {VALID_MARKETS}")
    allowed = VALID_SELECTIONS[obs.market]
    if obs.selection not in allowed:
        raise PriceEntryError(
            f"selection {obs.selection!r} is not valid for {obs.market}; expected {allowed}"
        )
    if obs.market in ("SPREAD", "TOTAL") and obs.line is None:
        raise PriceEntryError(f"{obs.market} requires a line")
    if obs.market == "MONEYLINE" and obs.line is not None:
        raise PriceEntryError("MONEYLINE has no line")
    if obs.american == 0 or -100 < obs.american < 100:
        # American odds between -100 and +100 exclusive are not expressible.
        raise PriceEntryError(f"{obs.american} is not a valid American price")
    if obs.observed_at.tzinfo is None:
        raise PriceEntryError("observed_at must be timezone-aware")
    if obs.observed_at > now:
        # A price observed in the future is either a clock error or a
        # fabricated entry. Either way it must not become the basis of an
        # evaluation that claims to be point-in-time.
        raise PriceEntryError(
            f"observed_at {obs.observed_at.isoformat()} is after the current time "
            f"{now.isoformat()}; a price cannot have been seen in the future"
        )
    if not obs.user_id.strip():
        raise PriceEntryError("a manually entered price requires the identity of the enterer")
    # The fixture/live boundary, enforced at the point of entry rather than
    # discovered later: a fixture price must never be recorded as though a
    # person read it off a live book.
    if obs.source == SOURCE_MANUAL and obs.provider_mode is ProviderMode.FIXTURE:
        raise PriceEntryError(
            "a price sourced from a real book may not be recorded under FIXTURE mode"
        )
    if obs.source == SOURCE_FIXTURE and obs.provider_mode is ProviderMode.LIVE:
        raise PriceEntryError(
            "a fixture price may not be recorded as live provider output"
        )
    if obs.cohort is not Cohort.FIXTURE and obs.source == SOURCE_FIXTURE:
        raise PriceEntryError(
            f"a fixture price may not enter the {obs.cohort.value} cohort; "
            "fixture observations stay in the fixture cohort"
        )


def record_price_observation(
    session: Session,
    obs: PriceObservation,
    *,
    now: datetime | None = None,
    correction_of_id: str | None = None,
    correction_reason: str | None = None,
) -> ManualBookPriceEntry:
    """Persist one immutable price observation.

    A correction supersedes rather than edits: the prior row keeps its
    values and gains a `superseded_by_id`, and the new row records what it
    corrects and why. Nothing that was once believed is erased.
    """
    now = now or utc_now()
    _validate(obs, now=now)

    prior: ManualBookPriceEntry | None = None
    if correction_of_id is not None:
        if not (correction_reason or "").strip():
            raise PriceEntryError("a correction requires a reason")
        prior = session.get(ManualBookPriceEntry, correction_of_id)
        if prior is None:
            raise PriceEntryError(f"cannot correct unknown price entry {correction_of_id}")
        if prior.superseded_by_id is not None:
            raise PriceEntryError(
                f"price entry {correction_of_id} was already superseded by "
                f"{prior.superseded_by_id}; correct the current row instead"
            )

    # Line and price are NOT in the logical identity. The same submission
    # reporting a different number is exactly the case that must surface: a
    # person re-entering one observation with a different price has either
    # mistyped or is looking at a changed market, and storing both as
    # equally valid observations of one moment hides that.
    from fde_api.forward.domain_identity import PRICE_OBSERVATION, upsert_by_identity

    entry = ManualBookPriceEntry(
        id=f"px_{uuid.uuid4().hex[:20]}",
        data_mode=obs.data_mode.value,
        canonical_game_id=obs.canonical_game_id,
        market=obs.market,
        selection=obs.selection,
        line=obs.line,
        american=obs.american,
        source=obs.source,
        observed_at=obs.observed_at,
        entered_at=now,
        confirmed_at=now if obs.confirmed else None,
        correction_of_id=correction_of_id,
        correction_reason=correction_reason,
        user_id=obs.user_id,
        cohort=obs.cohort.value,
        provider_mode=obs.provider_mode.value,
        policy_version=obs.policy_version,
        code_commit=current_code_commit(),
        decimal_odds=american_to_decimal(obs.american),
        break_even_probability=break_even_prob(obs.american),
    )
    logical = {
        "canonical_game_id": obs.canonical_game_id,
        "market": obs.market,
        "selection": obs.selection,
        "observed_at": obs.observed_at,
        "cohort": obs.cohort.value,
        "provider_mode": obs.provider_mode.value,
        # A correction is a NEW governed identity, never an edit, so the
        # correction target participates in the slot.
        "source_identity": f"{obs.user_id}|{correction_of_id or '-'}",
    }
    content = {
        "line": obs.line,
        "american": obs.american,
        "decimal_odds": entry.decimal_odds,
        "break_even_probability": entry.break_even_probability,
        "confirmed": obs.confirmed,
        "source": obs.source,
        "policy_version": obs.policy_version,
        "code_commit": entry.code_commit,
    }
    result = upsert_by_identity(
        session, ManualBookPriceEntry, identity=PRICE_OBSERVATION,
        logical_values=logical, content_values=content, build=lambda: entry,
    )
    if result.conflicted:
        raise PriceEntryError(
            f"a price for {obs.market}/{obs.selection} observed at "
            f"{obs.observed_at.isoformat()} was already submitted under this "
            f"identity with different content (existing "
            f"{(result.existing_content_hash or '')[:12]}, submitted "
            f"{result.content_hash[:12]}). The original stands; submit a "
            "correction if the new value is right."
        )

    if prior is not None and result.created:
        prior.superseded_by_id = result.record.id
        session.flush()
    return result.record


def confirm_price_observation(
    session: Session, entry_id: str, *, now: datetime | None = None
) -> ManualBookPriceEntry:
    """Mark an entry as re-read by the person who entered it.

    Confirmation is the only mutation permitted on a price row, and it is
    one-way: it records that a check happened, and never un-happens.
    """
    entry = session.get(ManualBookPriceEntry, entry_id)
    if entry is None:
        raise PriceEntryError(f"unknown price entry {entry_id}")
    if entry.superseded_by_id is not None:
        raise PriceEntryError(f"price entry {entry_id} was superseded; confirm the current row")
    if entry.confirmed_at is None:
        entry.confirmed_at = now or utc_now()
        session.flush()
    return entry


def current_price(
    session: Session,
    *,
    canonical_game_id: str,
    market: str,
    selection: str,
    cohort: Cohort,
    as_of: datetime | None = None,
) -> ManualBookPriceEntry | None:
    """The newest non-superseded price for a selection, as of a moment.

    `as_of` filters on `observed_at`, not `entered_at`: a price typed in
    late but observed early was knowable at the time it was observed, and a
    price observed after the cutoff is not knowable at the cutoff no matter
    when it was typed.
    """
    stmt = select(ManualBookPriceEntry).where(
        ManualBookPriceEntry.canonical_game_id == canonical_game_id,
        ManualBookPriceEntry.market == market,
        ManualBookPriceEntry.selection == selection,
        ManualBookPriceEntry.cohort == cohort.value,
        ManualBookPriceEntry.superseded_by_id.is_(None),
    )
    if as_of is not None:
        stmt = stmt.where(ManualBookPriceEntry.observed_at <= as_of)
    # Deterministic even when two rows share an instant: id breaks the tie,
    # so the same database always answers the same way. The rule lives in
    # `ordering` because all three call sites need the identical one, and an
    # inline copy of it turned out to be untestable.
    return newest(
        list(session.scalars(stmt)),
        when=lambda r: r.observed_at,
        ident=lambda r: r.id,
    )


def price_age_seconds(entry: ManualBookPriceEntry, *, as_of: datetime) -> int:
    """How stale the price was at `as_of`, from when the USER saw it."""
    return max(0, int((as_of - entry.observed_at).total_seconds()))


def describe(entry: ManualBookPriceEntry) -> dict[str, Any]:
    """Every governance field, for lineage and audit.

    Deliberately says `observed_at_utc` rather than anything resembling
    "current": this is a record of a price that was seen, not a statement
    that it is still available.
    """
    return {
        "id": entry.id,
        "canonical_game_id": entry.canonical_game_id,
        "market": entry.market,
        "selection": entry.selection,
        "line": entry.line,
        "american": entry.american,
        "decimal_odds": entry.decimal_odds,
        "break_even_probability": entry.break_even_probability,
        "source": entry.source,
        "cohort": entry.cohort,
        "provider_mode": entry.provider_mode,
        "policy_version": entry.policy_version,
        "code_commit": entry.code_commit,
        "data_mode": entry.data_mode,
        "observed_at_utc": entry.observed_at.isoformat(),
        "entered_at_utc": entry.entered_at.isoformat(),
        "confirmed": entry.confirmed_at is not None,
        "confirmed_at_utc": entry.confirmed_at.isoformat() if entry.confirmed_at else None,
        "superseded_by_id": entry.superseded_by_id,
        "correction_of_id": entry.correction_of_id,
        "user_id": entry.user_id,
    }
