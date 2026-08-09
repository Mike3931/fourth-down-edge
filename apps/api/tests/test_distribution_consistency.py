"""The guarantee the distribution module makes about itself.

Every model in the registry emits moments and nothing else; this module
turns them into every probability the application shows. Its docstring
promises that those probabilities "cannot disagree with each other",
which is the reason no model can publish a moneyline that contradicts its
own spread.

The promise had no stated range and nothing enforced one. `spread_probs`
and `total_probs` divided by the retained mass of the truncated pmf;
`home_win_prob` summed the same pmf and did not. At the sigma the fitted
models actually produce (12-14, fallback 13.5) the two agree to 1e-14, so
nothing shipped was wrong. At sigma 50 they differed by 2.3 percentage
points and at sigma 80 by 10.6 - silently, because both numbers remain
perfectly plausible probabilities.

These tests assert the invariant across a sigma range wide enough to
catch that, rather than at the one value the current models happen to
emit. A guarantee that holds only where it was spot-checked is a
coincidence.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from fde_api.models_ml.distribution import GameDistribution, MuSigma

WIDE_SIGMAS = [5.0, 10.0, 13.5, 20.0, 35.0, 50.0, 80.0]


def _dist(mu_margin: float, sigma_margin: float) -> GameDistribution:
    return GameDistribution(MuSigma(mu_margin, sigma_margin), MuSigma(44.0, 10.0))


class TestTheMoneylineAgreesWithItsOwnSpread:
    @pytest.mark.parametrize("sigma", WIDE_SIGMAS)
    @pytest.mark.parametrize("mu", [-7.0, 0.0, 2.5, 10.0])
    def test_home_win_equals_cover_plus_half_the_push_at_a_zero_line(
        self, mu: float, sigma: float
    ) -> None:
        """A pick-em spread and a moneyline are the same question. The two
        code paths must return the same answer, not merely a similar one."""
        d = _dist(mu, sigma)
        cover, push, _ = d.spread_probs(0.0)
        assert d.home_win_prob() == pytest.approx(cover + 0.5 * push, abs=1e-9)


class TestEveryProbabilitySetIsNormalised:
    @pytest.mark.parametrize("sigma", WIDE_SIGMAS)
    def test_spread_outcomes_sum_to_one(self, sigma: float) -> None:
        cover, push, lose = _dist(1.0, sigma).spread_probs(-3.0)
        assert cover + push + lose == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.parametrize("sigma", WIDE_SIGMAS)
    def test_win_probability_stays_a_probability(self, sigma: float) -> None:
        p = _dist(3.0, sigma).home_win_prob()
        assert 0.0 <= p <= 1.0

    @pytest.mark.parametrize("sigma", WIDE_SIGMAS)
    def test_a_symmetric_margin_gives_an_even_game(self, sigma: float) -> None:
        """mu=0 must give exactly 0.5 once ties are split. Unnormalised,
        the truncated tails pulled this below 0.5 as sigma grew."""
        assert _dist(0.0, sigma).home_win_prob() == pytest.approx(0.5, abs=1e-9)


class TestMoreFavourableLinesNeverPriceWorse:
    @pytest.mark.parametrize("sigma", WIDE_SIGMAS)
    def test_cover_probability_is_monotone_in_the_line(self, sigma: float) -> None:
        d = _dist(1.5, sigma)
        lines = [-10.0, -7.0, -3.5, -3.0, -0.5, 0.0, 0.5, 3.0, 7.0, 10.0]
        covers = [d.spread_probs(line)[0] for line in lines]
        for earlier, later in pairwise(covers):
            assert later >= earlier - 1e-12


class TestTheIntervalIsWhatItSaysItIs:
    def test_it_is_the_continuous_quantile_not_the_discrete_one(self) -> None:
        """Documented explicitly because the module previously described
        the interval and the probabilities as sharing one source. They do
        not: this is mu +/- 1.2816*sigma, reported unrounded."""
        d = _dist(2.0, 13.5)
        lo, hi, _, _ = d.interval_80()
        assert lo == pytest.approx(2.0 - 1.2815515655446004 * 13.5, abs=1e-9)
        assert hi == pytest.approx(2.0 + 1.2815515655446004 * 13.5, abs=1e-9)
        assert lo != int(lo), "a continuous quantile should not land on an integer by accident"
