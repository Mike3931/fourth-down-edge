"""Independent probability calibration.

Candidate methods are fitted on PRIOR out-of-fold predictions only and
selected by log loss there; the final test period never touches the
fit. Artifacts are versioned rows (CalibrationArtifact) carrying their
parameters, fitting window, and sample size, and every report includes
the identity (no-calibration) comparison.

Methods:
    none      identity
    platt     logistic recalibration on logit(p)  (2 params)
    beta      Beta calibration: logistic on [ln p, −ln(1−p)]  (3 params)
    isotonic  only offered when n >= MIN_ISOTONIC_N; stored as knots
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log
from typing import Any

import numpy as np

from fde_api.evaluation import clamp01, log_loss

MIN_ISOTONIC_N = 800


@dataclass
class FittedCalibration:
    method: str
    parameters: dict[str, Any]
    sample_size: int

    def apply(self, p: float) -> float:
        p = clamp01(p)
        if self.method == "none":
            return p
        if self.method == "platt":
            a, b = self.parameters["a"], self.parameters["b"]
            z = a + b * log(p / (1 - p))
            return 1 / (1 + exp(-z))
        if self.method == "beta":
            a, b, c = self.parameters["a"], self.parameters["b"], self.parameters["c"]
            z = c + a * log(p) - b * log(1 - p)
            return 1 / (1 + exp(-z))
        if self.method == "isotonic":
            xs = np.array(self.parameters["x"])
            ys = np.array(self.parameters["y"])
            return float(np.clip(np.interp(p, xs, ys), 1e-6, 1 - 1e-6))
        raise ValueError(f"unknown calibration method {self.method}")


def _fit_logistic(features: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta = np.zeros(features.shape[1])
    for _ in range(100):
        eta = features @ beta
        mu = 1 / (1 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-6, None)
        grad = features.T @ (y - mu)
        hess = features.T @ (features * w[:, None]) + 1e-8 * np.eye(features.shape[1])
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.abs(step).max() < 1e-10:
            break
    return beta


def fit_calibration(method: str, probs: list[float], outcomes: list[int]) -> FittedCalibration:
    n = len(probs)
    p = np.array([clamp01(v) for v in probs])
    y = np.array(outcomes, dtype=float)
    if method == "none":
        return FittedCalibration("none", {}, n)
    if method == "platt":
        x = np.column_stack([np.ones(n), np.log(p / (1 - p))])
        beta = _fit_logistic(x, y)
        return FittedCalibration("platt", {"a": float(beta[0]), "b": float(beta[1])}, n)
    if method == "beta":
        x = np.column_stack([np.ones(n), np.log(p), -np.log(1 - p)])
        beta = _fit_logistic(x, y)
        return FittedCalibration(
            "beta", {"c": float(beta[0]), "a": float(beta[1]), "b": float(beta[2])}, n
        )
    if method == "isotonic":
        if n < MIN_ISOTONIC_N:
            raise ValueError(f"isotonic requires n >= {MIN_ISOTONIC_N}, got {n}")
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
        iso.fit(p, y)
        xs = np.linspace(0, 1, 101)
        return FittedCalibration(
            "isotonic", {"x": xs.tolist(), "y": iso.predict(xs).tolist()}, n
        )
    raise ValueError(f"unknown calibration method {method}")


def select_calibration(
    probs: list[float], outcomes: list[int]
) -> tuple[FittedCalibration, dict[str, float]]:
    """Fit all eligible methods on the prior OOF sample; pick by log loss.
    Returns the winner plus every candidate's score (for the report —
    including the identity comparison)."""
    candidates = ["none", "platt", "beta"]
    if len(probs) >= MIN_ISOTONIC_N:
        candidates.append("isotonic")
    scores: dict[str, float] = {}
    fits: dict[str, FittedCalibration] = {}
    for m in candidates:
        fit = fit_calibration(m, probs, outcomes)
        applied = [fit.apply(p) for p in probs]
        scores[m] = log_loss(applied, outcomes)
        fits[m] = fit
    best = min(scores, key=lambda m: scores[m])
    return fits[best], scores
