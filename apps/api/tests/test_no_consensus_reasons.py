"""Why the screen says a market could not be priced.

The Live Slate showed "0 eligible book(s) in the current window, minimum
is 3" immediately above a table headed "Captured quotes (6 from
draftkings)". Both statements were true. Together they read as a bug: six
quotes are visibly present, and the line above appears to deny it.

The missing word was BETWEEN them. The quotes existed and were EXCLUDED,
for a reason `select_eligible_quotes` had already computed and the
endpoint discarded on its way to the response.

These tests pin two things that matter more than the wording:

  * the binding constraint is still stated FIRST, so no added detail can
    soften "this market was not priced" into something that sounds like a
    price;
  * a category is mentioned only when it actually excluded something, so
    the explanation never pads itself with zeroes and never invents a
    cause the filter did not record.
"""

from __future__ import annotations

from fde_api.api.main import _no_consensus_reasons
from fde_api.forward.consensus import EligibilityReport


def _report(**kw: int) -> EligibilityReport:
    rep = EligibilityReport()
    for key, value in kw.items():
        setattr(rep, key, value)
    return rep


class TestTheBindingConstraintIsAlwaysStatedFirst:
    def test_the_book_minimum_leads_every_explanation(self) -> None:
        reasons = _no_consensus_reasons(
            0, _report(considered=6, rejected_stale=6)
        )
        assert reasons[0].startswith("no consensus captured yet")
        assert "minimum is 3" in reasons[0]

    def test_it_leads_even_when_nothing_was_captured(self) -> None:
        reasons = _no_consensus_reasons(0, _report())
        assert reasons[0].startswith("no consensus captured yet")

    def test_no_reason_ever_states_a_line_or_a_price(self) -> None:
        """The guard that matters: this text sits where a consensus would
        be. It must not read as one."""
        reasons = _no_consensus_reasons(
            2, _report(considered=9, rejected_stale=4, rejected_live=3,
                       rejected_unknown_book=2)
        )
        joined = " ".join(reasons).lower()
        for forbidden in ("median", "consensus is", "fair value", "edge", "probability"):
            assert forbidden not in joined


class TestQuotesThatExistAreDistinguishedFromQuotesThatDoNot:
    def test_nothing_captured_says_so_plainly(self) -> None:
        reasons = _no_consensus_reasons(0, _report(considered=0))
        assert any("no quotes have been captured" in r for r in reasons)

    def test_captured_but_excluded_reports_the_count_and_the_cause(self) -> None:
        """The case that produced the apparent contradiction."""
        reasons = _no_consensus_reasons(
            0, _report(considered=6, rejected_stale=6)
        )
        detail = " ".join(reasons[1:])
        assert "6 quote(s) were considered and excluded" in detail
        assert "freshness window" in detail

    def test_an_empty_market_never_claims_quotes_were_excluded(self) -> None:
        reasons = _no_consensus_reasons(0, _report(considered=0))
        assert not any("excluded" in r for r in reasons)


class TestACategoryAppearsOnlyWhenItExcludedSomething:
    def test_zero_counts_are_omitted_rather_than_listed(self) -> None:
        reasons = _no_consensus_reasons(
            0, _report(considered=4, rejected_stale=4)
        )
        detail = " ".join(reasons)
        assert "in-play" not in detail
        assert "recognised list" not in detail
        assert "implausible" not in detail

    def test_every_nonzero_category_is_named(self) -> None:
        reasons = _no_consensus_reasons(
            0,
            _report(considered=10, rejected_stale=1, rejected_live=2,
                    rejected_unknown_book=3, rejected_invalid=1,
                    rejected_duplicate=3),
        )
        detail = " ".join(reasons)
        for fragment in ("freshness window", "in-play", "recognised list",
                         "implausible", "superseded"):
            assert fragment in detail, fragment

    def test_counts_are_reported_not_recomputed(self) -> None:
        """The numbers come from the filter. Nothing here re-derives them,
        because a second derivation is a second thing that can disagree."""
        reasons = _no_consensus_reasons(
            0, _report(considered=7, rejected_unknown_book=7)
        )
        detail = " ".join(reasons)
        assert "7 quote(s) were considered" in detail
        assert "7 from a book that is not on the recognised list" in detail

    def test_a_partially_eligible_market_still_explains_the_shortfall(self) -> None:
        """Two books eligible, minimum three: the market is unpriced and
        the reason is the minimum, not an absence of data."""
        reasons = _no_consensus_reasons(
            2, _report(considered=5, eligible=2, rejected_stale=3)
        )
        assert "2 eligible book(s)" in reasons[0]
        assert any("excluded" in r for r in reasons[1:])
