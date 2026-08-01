"""Proper scoring and evaluation metrics.

Primary: log loss, Brier, calibration intercept/slope, reliability
curve, CRPS (closed form for the Normal margin/total forecasts).
Secondary: MAE, win/cover/over rates. ROI and drawdown live in the
backtest module because they depend on execution assumptions.

Confidence intervals use week-block bootstrap: whole weeks are resampled
so intra-week correlation (shared slates, weather, refs) is respected.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, log, pi, sqrt

import numpy as np

_EPS = 1e-12


def clamp01(p: float) -> float:
    return min(1.0 - 1e-9, max(1e-9, p))


def log_loss(probs: list[float], outcomes: list[int]) -> float:
    assert len(probs) == len(outcomes) and probs
    return -sum(
        log(clamp01(p)) if y == 1 else log(clamp01(1 - p)) for p, y in zip(probs, outcomes, strict=True)
    ) / len(probs)


def brier(probs: list[float], outcomes: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes, strict=True)) / len(probs)


def calibration_line(probs: list[float], outcomes: list[int]) -> tuple[float, float]:
    """(intercept, slope) of a logistic recalibration fit y ~ logit(p).
    Perfect calibration → (0, 1). Newton iterations, no sklearn needed."""
    x = np.array([log(clamp01(p) / (1 - clamp01(p))) for p in probs])
    y = np.array(outcomes, dtype=float)
    beta = np.zeros(2)
    xmat = np.column_stack([np.ones_like(x), x])
    for _ in range(50):
        eta = xmat @ beta
        mu = 1 / (1 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-6, None)
        grad = xmat.T @ (y - mu)
        hess = xmat.T @ (xmat * w[:, None])
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        beta += step
        if np.abs(step).max() < 1e-10:
            break
    return float(beta[0]), float(beta[1])


def reliability_bins(probs: list[float], outcomes: list[int], n_bins: int = 10) -> list[dict[str, float]]:
    bins: list[dict[str, float]] = []
    edges = np.linspace(0, 1, n_bins + 1)
    p = np.array(probs)
    y = np.array(outcomes, dtype=float)
    for i in range(n_bins):
        mask = (p >= edges[i]) & (p < edges[i + 1] if i < n_bins - 1 else p <= edges[i + 1])
        if mask.sum() == 0:
            continue
        bins.append(
            {
                "bin_low": float(edges[i]),
                "bin_high": float(edges[i + 1]),
                "predicted": float(p[mask].mean()),
                "observed": float(y[mask].mean()),
                "count": int(mask.sum()),
            }
        )
    return bins


def crps_normal(mu: float, sigma: float, actual: float) -> float:
    """Closed-form CRPS for a Normal forecast."""
    z = (actual - mu) / sigma
    pdf = exp(-z * z / 2) / sqrt(2 * pi)
    cdf = 0.5 * (1 + erf(z / sqrt(2)))
    return sigma * (z * (2 * cdf - 1) + 2 * pdf - 1 / sqrt(pi))


def mae(preds: list[float], actuals: list[float]) -> float:
    return sum(abs(p - a) for p, a in zip(preds, actuals, strict=True)) / len(preds)


@dataclass
class BlockBootstrapCI:
    mean: float
    lo: float
    hi: float
    n_blocks: int


def week_block_bootstrap(
    values: list[float], weeks: list[str], n_boot: int = 2000, seed: int = 7, alpha: float = 0.1
) -> BlockBootstrapCI:
    """CI for the mean of `values` resampling whole week-blocks."""
    rng = np.random.default_rng(seed)
    by_week: dict[str, list[float]] = {}
    for v, w in zip(values, weeks, strict=True):
        by_week.setdefault(w, []).append(v)
    blocks = list(by_week.values())
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(blocks), size=len(blocks))
        sample = [v for i in pick for v in blocks[i]]
        means.append(float(np.mean(sample)))
    return BlockBootstrapCI(
        mean=float(np.mean(values)),
        lo=float(np.quantile(means, alpha / 2)),
        hi=float(np.quantile(means, 1 - alpha / 2)),
        n_blocks=len(blocks),
    )
