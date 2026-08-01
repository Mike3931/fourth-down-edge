"""Property-based tests for the shared outcome distribution."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from fde_api.models_ml.distribution import GameDistribution, MuSigma

mus = st.floats(min_value=-30, max_value=30)
sigmas = st.floats(min_value=3, max_value=25)
total_mus = st.floats(min_value=20, max_value=70)
lines = st.floats(min_value=-20, max_value=20).map(lambda x: round(x * 2) / 2)
total_lines = st.floats(min_value=30, max_value=60).map(lambda x: round(x * 2) / 2)


def _dist(mu: float, sigma: float, mu_t: float, sigma_t: float) -> GameDistribution:
    return GameDistribution(MuSigma(mu, sigma), MuSigma(mu_t, sigma_t))


@settings(max_examples=200)
@given(mus, sigmas, total_mus, sigmas)
def test_probabilities_in_unit_interval(mu, sigma, mu_t, sigma_t) -> None:
    d = _dist(mu, sigma, mu_t, sigma_t)
    p = d.home_win_prob()
    assert 0.0 <= p <= 1.0
    cover, push, lose = d.spread_probs(-3.5)
    assert abs(cover + push + lose - 1.0) < 1e-9
    assert all(0 <= x <= 1 for x in (cover, push, lose))


@settings(max_examples=200)
@given(mus, sigmas, total_mus, sigmas, lines)
def test_more_favorable_spread_never_lowers_cover_prob(mu, sigma, mu_t, sigma_t, line) -> None:
    d = _dist(mu, sigma, mu_t, sigma_t)
    c1, _, _ = d.spread_probs(line)
    c2, _, _ = d.spread_probs(line + 0.5)  # home gets half a point more
    assert c2 >= c1 - 1e-12


@settings(max_examples=200)
@given(mus, sigmas, total_mus, sigmas)
def test_interval_bounds_ordered(mu, sigma, mu_t, sigma_t) -> None:
    d = _dist(mu, sigma, mu_t, sigma_t)
    m_lo, m_hi, t_lo, t_hi = d.interval_80()
    assert m_lo < m_hi and t_lo < t_hi


@settings(max_examples=100)
@given(mus, sigmas, total_mus, sigmas, lines, total_lines)
def test_outputs_are_coherent(mu, sigma, mu_t, sigma_t, line, t_line) -> None:
    d = _dist(mu, sigma, mu_t, sigma_t)
    out = d.outputs(line, t_line)
    # Same distribution generates all probabilities: pushes only on integers.
    if line % 1 != 0:
        assert out["spread_push_prob"] == 0.0
    if t_line % 1 != 0:
        assert out["total_push_prob"] == 0.0
    assert abs(out["expected_home_points"] + out["expected_away_points"] - out["expected_total"]) < 1e-9
    assert abs(
        out["expected_home_points"] - out["expected_away_points"] - out["expected_margin"]
    ) < 1e-9


def test_integer_line_has_push_mass() -> None:
    d = _dist(0.0, 6.0, 44.0, 9.0)
    _, push, _ = d.spread_probs(-3.0)
    assert push > 0.01


def test_no_nan_outputs() -> None:
    import math

    d = _dist(3.0, 13.5, 44.5, 10.0)
    out = d.outputs(-3.5, 44.5)
    for k, v in out.items():
        if isinstance(v, float):
            assert not math.isnan(v), k
