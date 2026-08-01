"""Calibration: methods, selection, identity comparison, guards."""

from __future__ import annotations

import random

from fde_api.calibration import fit_calibration, select_calibration
from fde_api.evaluation import calibration_line, log_loss, reliability_bins


def _miscalibrated_sample(n: int = 3000, seed: int = 3):
    """True prob t; reported prob is overconfident: p = t**0.5 shifted."""
    rng = random.Random(seed)
    probs, outcomes = [], []
    for _ in range(n):
        t = rng.uniform(0.05, 0.95)
        reported = min(0.99, max(0.01, 0.5 + (t - 0.5) * 1.8))  # overconfident
        probs.append(reported)
        outcomes.append(1 if rng.random() < t else 0)
    return probs, outcomes


def test_platt_improves_overconfident_probabilities() -> None:
    probs, outcomes = _miscalibrated_sample()
    raw_ll = log_loss(probs, outcomes)
    fit = fit_calibration("platt", probs, outcomes)
    cal_ll = log_loss([fit.apply(p) for p in probs], outcomes)
    assert cal_ll < raw_ll


def test_selection_reports_identity_comparison() -> None:
    probs, outcomes = _miscalibrated_sample()
    best, scores = select_calibration(probs, outcomes)
    assert "none" in scores  # identity/no-change comparison always present
    assert best.method in scores
    assert scores[best.method] <= min(scores.values()) + 1e-12
    assert best.sample_size == len(probs)


def test_isotonic_offered_only_with_sample() -> None:
    probs, outcomes = _miscalibrated_sample(n=100)
    _, scores = select_calibration(probs, outcomes)
    assert "isotonic" not in scores
    probs, outcomes = _miscalibrated_sample(n=1200)
    _, scores = select_calibration(probs, outcomes)
    assert "isotonic" in scores


def test_calibration_line_recovers_identity() -> None:
    rng = random.Random(5)
    probs, outcomes = [], []
    for _ in range(4000):
        t = rng.uniform(0.05, 0.95)
        probs.append(t)
        outcomes.append(1 if rng.random() < t else 0)
    intercept, slope = calibration_line(probs, outcomes)
    assert abs(intercept) < 0.15 and abs(slope - 1.0) < 0.15


def test_reliability_bins_shape() -> None:
    probs, outcomes = _miscalibrated_sample()
    bins = reliability_bins(probs, outcomes)
    assert bins and all(0 <= b["predicted"] <= 1 and 0 <= b["observed"] <= 1 for b in bins)
    assert sum(b["count"] for b in bins) == len(probs)
