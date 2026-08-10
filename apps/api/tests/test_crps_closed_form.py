"""CRPS checked against its definition, not against itself.

`crps_normal` is reported in every walk-forward as `crps_margin` and
`crps_total`, and it is the only metric here that scores the whole
predictive distribution rather than a probability or a point. A wrong
constant in the closed form would produce numbers that are smooth,
positive, ordered sensibly by error size, and simply wrong — nothing
about the reports would look off.

So it is verified by numerically integrating the definition

    CRPS(F, y) = ∫ (F(x) − 1{x ≥ y})² dx

which shares no algebra with the implementation. A transcription error in
`z(2Φ(z) − 1) + 2φ(z) − 1/√π` cannot survive it; a test that re-derived
the same closed form would confirm the typo instead.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from scipy.integrate import quad
from scipy.stats import norm

from fde_api.evaluation import crps_normal

CASES = [
    (0.0, 1.0, 0.0),      # standard normal, actual at the mean
    (0.0, 1.0, 1.5),
    (0.0, 1.0, -2.3),
    (2.5, 13.5, 7.0),     # the operating point: NFL margin moments
    (2.5, 13.5, -14.0),
    (44.0, 10.0, 52.0),   # totals
    (-3.0, 13.5, -3.0),
    (0.0, 0.5, 3.0),      # tight sigma, far tail
    (100.0, 25.0, -10.0),
]


def _crps_by_integration(mu: float, sigma: float, actual: float) -> float:
    def integrand(x: float) -> float:
        return (norm.cdf(x, mu, sigma) - (1.0 if x >= actual else 0.0)) ** 2

    lo, hi = mu - 40 * sigma, mu + 40 * sigma
    # Split at the step so the quadrature never straddles the discontinuity.
    left, _ = quad(integrand, lo, actual, limit=400)
    right, _ = quad(integrand, actual, hi, limit=400)
    return left + right


class TestTheClosedFormMatchesTheDefinition:
    @pytest.mark.parametrize(("mu", "sigma", "actual"), CASES)
    def test_it_agrees_with_numerical_integration(
        self, mu: float, sigma: float, actual: float
    ) -> None:
        assert crps_normal(mu, sigma, actual) == pytest.approx(
            _crps_by_integration(mu, sigma, actual), rel=1e-9
        )


class TestTheScoringRuleBehavesLikeOne:
    def test_it_is_never_negative(self) -> None:
        for mu, sigma, actual in CASES:
            assert crps_normal(mu, sigma, actual) >= 0.0

    def test_it_grows_as_the_forecast_gets_further_from_the_outcome(self) -> None:
        """Monotone in |actual − mu| at fixed sigma. A sign error on the
        `z(2Φ(z) − 1)` term would invert this."""
        base = crps_normal(2.5, 13.5, 2.5)
        worse = [crps_normal(2.5, 13.5, 2.5 + d) for d in (5, 10, 20, 30)]
        assert base < worse[0]
        for earlier, later in pairwise(worse):
            assert later > earlier

    def test_it_is_symmetric_about_the_mean(self) -> None:
        for d in (1.0, 7.0, 25.0):
            assert crps_normal(2.5, 13.5, 2.5 + d) == pytest.approx(
                crps_normal(2.5, 13.5, 2.5 - d), rel=1e-12
            )

    def test_it_scales_linearly_with_sigma(self) -> None:
        """CRPS(mu, c·sigma, mu + c·d) == c · CRPS(mu, sigma, mu + d).
        Catches a misplaced sigma outside versus inside the bracket."""
        for c in (2.0, 0.5, 10.0):
            assert crps_normal(0.0, c * 13.5, c * 7.0) == pytest.approx(
                c * crps_normal(0.0, 13.5, 7.0), rel=1e-12
            )

    def test_a_perfect_point_forecast_still_costs_something(self) -> None:
        """CRPS charges for spread even when the mean is exactly right,
        which is the property that makes it a distributional score rather
        than a dressed-up absolute error."""
        assert crps_normal(2.5, 13.5, 2.5) > 0.0
        assert crps_normal(2.5, 1.0, 2.5) < crps_normal(2.5, 13.5, 2.5)
