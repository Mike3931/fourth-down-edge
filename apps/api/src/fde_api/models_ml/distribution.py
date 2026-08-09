"""Documented distributional baseline for game outcomes.

Margin (home − away) and total are modeled as independent Normals whose
moments come from the upstream model, then *discretized to integer
outcomes* by unit-width bins:

    P(M = k) = Φ((k+0.5−μ)/σ) − Φ((k−0.5−μ)/σ)

Every PROBABILITY downstream — win, cover, over, push — is computed from
that one discrete distribution and normalized by its retained mass, so
moneyline/spread/total probabilities cannot disagree with each other,
pushes have real mass on integer lines, and a more favorable line can
never produce a lower cover probability (verified property-based).

The 80% interval is the exception and is stated separately because it is
not derived from the pmf at all: it is the continuous Normal quantile,
mu ± 1.2816·sigma, reported unrounded. At sigma 13.5 that puts the
margin p10 at −15.30 where the discrete pmf puts it at −15 — close, but
they are two different objects, and the earlier wording claimed one
source for both.

Documented limitations: independence of margin and total is an
approximation; ties are folded into the margin distribution's zero bin
and split evenly for win probability; real NFL margins concentrate on
key numbers (3, 7) more than a Normal does — a v2 candidate is a
key-number-reweighted margin distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt

_RANGE = 100  # supported integer outcomes: margins −100..100, totals 0..120


def _phi(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


@dataclass(frozen=True)
class MuSigma:
    mu: float
    sigma: float

    def __post_init__(self) -> None:
        if not (self.sigma > 0):
            raise ValueError(f"sigma must be positive, got {self.sigma}")


class GameDistribution:
    def __init__(self, margin: MuSigma, total: MuSigma) -> None:
        self.margin = margin
        self.total = total

    # -- discrete pmf helpers ------------------------------------------- #

    @staticmethod
    def _pmf(ms: MuSigma, lo: int, hi: int) -> dict[int, float]:
        out: dict[int, float] = {}
        for k in range(lo, hi + 1):
            p = _phi((k + 0.5 - ms.mu) / ms.sigma) - _phi((k - 0.5 - ms.mu) / ms.sigma)
            if p > 0:
                out[k] = p
        return out

    def margin_pmf(self) -> dict[int, float]:
        return self._pmf(self.margin, -_RANGE, _RANGE)

    def total_pmf(self) -> dict[int, float]:
        return self._pmf(self.total, 0, _RANGE + 20)

    # -- derived probabilities ------------------------------------------ #

    def home_win_prob(self) -> float:
        # Normalised by the retained mass, exactly as `spread_probs` and
        # `total_probs` are. The pmf is truncated to +/-_RANGE, so at large
        # sigma it sums to less than one, and a bare sum silently reports
        # the missing tail as neither a win nor a loss. Two of the three
        # methods divided by the mass and this one did not, which made the
        # module's own guarantee - that win, cover and total probabilities
        # cannot disagree - false: against `spread_probs(0)` this drifted
        # 2.3 points at sigma 50 and 10.6 at sigma 80.
        #
        # No model in the registry emits a sigma near those (fitted margin
        # sigma runs 12-14, and the hardcoded fallback is 13.5), so nothing
        # shipped was wrong. But the invariant is documented without a
        # stated range, nothing enforces one, and the divergence is silent
        # where it does bite.
        pmf = self.margin_pmf()
        mass = sum(pmf.values())
        if mass <= 0:
            return 0.5
        win = sum(p for k, p in pmf.items() if k > 0)
        tie = pmf.get(0, 0.0)
        return min(1.0, max(0.0, (win + 0.5 * tie) / mass))

    def spread_probs(self, home_line: float) -> tuple[float, float, float]:
        """(cover, push, lose) for the HOME side at `home_line`
        (home covers when margin + home_line > 0)."""
        pmf = self.margin_pmf()
        cover = push = lose = 0.0
        for k, p in pmf.items():
            v = k + home_line
            if v > 1e-9:
                cover += p
            elif v < -1e-9:
                lose += p
            else:
                push += p
        s = cover + push + lose
        return (cover / s, push / s, lose / s) if s > 0 else (0.5, 0.0, 0.5)

    def total_probs(self, total_line: float) -> tuple[float, float, float]:
        """(over, push, under) at `total_line`."""
        pmf = self.total_pmf()
        over = push = under = 0.0
        for k, p in pmf.items():
            if k > total_line + 1e-9:
                over += p
            elif k < total_line - 1e-9:
                under += p
            else:
                push += p
        s = over + push + under
        return (over / s, push / s, under / s) if s > 0 else (0.5, 0.0, 0.5)

    def interval_80(self) -> tuple[float, float, float, float]:
        """(margin_lo, margin_hi, total_lo, total_hi), 10th/90th pct."""
        z = 1.2815515655446004
        return (
            self.margin.mu - z * self.margin.sigma,
            self.margin.mu + z * self.margin.sigma,
            self.total.mu - z * self.total.sigma,
            self.total.mu + z * self.total.sigma,
        )

    def outputs(self, home_line: float | None, total_line: float | None) -> dict[str, float | None]:
        cover = push_s = over = push_t = None
        if home_line is not None:
            cover, push_s, _ = self.spread_probs(home_line)
        if total_line is not None:
            over, push_t, _ = self.total_probs(total_line)
        m_lo, m_hi, t_lo, t_hi = self.interval_80()
        exp_home = (self.total.mu + self.margin.mu) / 2
        exp_away = (self.total.mu - self.margin.mu) / 2
        return {
            "expected_home_points": exp_home,
            "expected_away_points": exp_away,
            "expected_margin": self.margin.mu,
            "expected_total": self.total.mu,
            "sigma_margin": self.margin.sigma,
            "sigma_total": self.total.sigma,
            "home_win_prob": self.home_win_prob(),
            "spread_cover_prob": cover,
            "spread_push_prob": push_s,
            "total_over_prob": over,
            "total_push_prob": push_t,
            "margin_p10": m_lo,
            "margin_p90": m_hi,
            "total_p10": t_lo,
            "total_p90": t_hi,
        }
