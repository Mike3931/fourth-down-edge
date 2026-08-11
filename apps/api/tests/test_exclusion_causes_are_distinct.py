"""Two opposite exclusions must not share one counter.

`select_eligible_quotes` rejected a quote observed AFTER the cutoff and a
quote observed too long BEFORE it into the same field, `rejected_stale`.
The endpoint then had no way to say which, and printed the disjunction
verbatim:

    2 quote(s) were considered and excluded: 2 older than the freshness
    window, or newer than the cutoff

The `or` is the code's ambiguity reaching the screen. The two causes have
opposite meanings and opposite remedies:

  * too old   - the price is probably gone; capture more often.
  * too new   - the observation is dated after the moment being priced.
                On the live path the cutoff IS now, so this means a
                timestamp in the future: a clock, a provider field, or a
                replay, and never something more capture would fix.

This is not hypothetical here. The development database holds twenty-four
quotes stamped 2026-09-10 against a 2026-08-11 clock, and once that
fixture's game enters the ten-day horizon the screen will explain them
with the same sentence it uses for an ordinary stale price.

`rejected_live` carried the same defect in a milder form: a quote the
PROVIDER flagged in-play and a quote observed after kickoff were counted
together under a message asserting the second. For a provider-flagged
quote before kickoff that assertion is simply false.

Every test below fails against the merged counters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fde_api.api.main import _no_consensus_reasons
from fde_api.forward.consensus import select_eligible_quotes

NOW = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)
KICKOFF = NOW + timedelta(days=3)


class FakeQuote:
    """Only the attributes the filter reads. A real OddsQuote would need a
    session, and the filter is a pure function over these fields."""

    def __init__(
        self,
        quote_id: int,
        observed_at: datetime,
        *,
        is_live: bool = False,
        sportsbook: str = "draftkings",
        market: str = "SPREAD",
        selection: str = "HOME",
        line: float | None = -3.0,
        american: int = -110,
    ) -> None:
        self.id = quote_id
        self.observed_at = observed_at
        self.is_live = is_live
        self.sportsbook = sportsbook
        self.market = market
        self.selection = selection
        self.line = line
        self.american = american


def _filter(quotes: list[Any], *, max_age_minutes: int = 60) -> Any:
    _, report = select_eligible_quotes(
        quotes, as_of_at=NOW, max_age_minutes=max_age_minutes, kickoff_utc=KICKOFF
    )
    return report


class TestStaleAndAfterCutoffAreCountedApart:
    def test_a_quote_older_than_the_window_is_stale_and_only_stale(self) -> None:
        rep = _filter([FakeQuote(1, NOW - timedelta(hours=9))])
        assert rep.rejected_stale == 1
        assert rep.rejected_after_cutoff == 0

    def test_a_quote_dated_after_the_cutoff_is_not_called_stale(self) -> None:
        """The exact shape sitting in the development database."""
        rep = _filter([FakeQuote(1, NOW + timedelta(days=30))])
        assert rep.rejected_after_cutoff == 1
        assert rep.rejected_stale == 0

    def test_both_at_once_are_reported_separately(self) -> None:
        rep = _filter([
            FakeQuote(1, NOW - timedelta(hours=9)),
            FakeQuote(2, NOW + timedelta(days=30), selection="AWAY"),
        ])
        assert (rep.rejected_stale, rep.rejected_after_cutoff) == (1, 1)

    def test_the_boundary_belongs_to_neither(self) -> None:
        """A quote exactly at the cutoff, and one exactly at the edge of
        the freshness window, are both admissible."""
        rep = _filter([
            FakeQuote(1, NOW),
            FakeQuote(2, NOW - timedelta(minutes=60), selection="AWAY"),
        ])
        assert rep.eligible == 2
        assert rep.rejected_stale == 0 and rep.rejected_after_cutoff == 0

    def test_every_considered_quote_lands_in_exactly_one_bucket(self) -> None:
        """The counters must partition. A quote counted twice, or not at
        all, makes the arithmetic on screen not add up."""
        rep = _filter([
            FakeQuote(1, NOW - timedelta(hours=9)),
            FakeQuote(2, NOW + timedelta(days=30), selection="AWAY"),
            FakeQuote(3, NOW - timedelta(minutes=5), is_live=True, market="TOTAL"),
            FakeQuote(4, NOW - timedelta(minutes=5), sportsbook="mystery-book"),
            FakeQuote(5, NOW - timedelta(minutes=5), american=-5, market="MONEYLINE"),
            FakeQuote(6, NOW - timedelta(minutes=5)),
            FakeQuote(7, NOW - timedelta(minutes=6)),  # duplicate of 6's slot
        ])
        buckets = (
            rep.eligible
            + rep.rejected_stale
            + rep.rejected_after_cutoff
            + rep.rejected_live
            + rep.rejected_post_kickoff
            + rep.rejected_unknown_book
            + rep.rejected_invalid
            + rep.rejected_duplicate
        )
        assert buckets == rep.considered == 7


class TestInPlayIsNotTheSameAsAfterKickoff:
    def test_a_provider_flagged_quote_before_kickoff_is_not_called_post_kickoff(
        self,
    ) -> None:
        rep = _filter([FakeQuote(1, NOW - timedelta(minutes=5), is_live=True)])
        assert rep.rejected_live == 1
        assert rep.rejected_post_kickoff == 0

    def test_a_quote_observed_after_kickoff_is_counted_as_such(self) -> None:
        """`as_of_at` is after kickoff here, so the cutoff does not catch
        it first and the kickoff rule is what excludes it."""
        _, rep = select_eligible_quotes(
            [FakeQuote(1, KICKOFF + timedelta(minutes=30))],
            as_of_at=KICKOFF + timedelta(hours=1),
            max_age_minutes=600,
            kickoff_utc=KICKOFF,
        )
        assert rep.rejected_post_kickoff == 1
        assert rep.rejected_live == 0


class TestTheScreenSaysWhichOneHappened:
    def test_the_two_causes_read_differently(self) -> None:
        stale = " ".join(
            _no_consensus_reasons(0, _filter([FakeQuote(1, NOW - timedelta(hours=9))]))
        )
        future = " ".join(
            _no_consensus_reasons(0, _filter([FakeQuote(1, NOW + timedelta(days=30))]))
        )
        assert stale != future

    def test_no_reason_offers_a_disjunction(self) -> None:
        """A diagnostic that says "A or B" has not diagnosed anything. The
        reader is being handed the code's uncertainty as if it were the
        market's."""
        rep = _filter([
            FakeQuote(1, NOW - timedelta(hours=9)),
            FakeQuote(2, NOW + timedelta(days=30), selection="AWAY"),
            FakeQuote(3, NOW - timedelta(minutes=5), is_live=True, market="TOTAL"),
        ])
        for reason in _no_consensus_reasons(0, rep):
            assert ", or " not in reason, reason

    def test_a_future_dated_quote_is_named_as_such(self) -> None:
        detail = " ".join(
            _no_consensus_reasons(0, _filter([FakeQuote(1, NOW + timedelta(days=30))]))
        )
        assert "freshness window" not in detail
        assert "cutoff" in detail

    def test_the_stale_wording_survives(self) -> None:
        """The message the original fix shipped still has to work for the
        case it was written for."""
        detail = " ".join(
            _no_consensus_reasons(0, _filter([FakeQuote(1, NOW - timedelta(hours=9))]))
        )
        assert "freshness window" in detail
