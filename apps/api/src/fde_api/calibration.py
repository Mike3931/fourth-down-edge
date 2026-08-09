"""Independent probability calibration.

Candidate methods are fitted on PRIOR out-of-fold model predictions only;
the final test period never touches the fit. Artifacts are versioned rows
(CalibrationArtifact) carrying their parameters, fitting window, and
sample size, and every report includes the identity (no-calibration)
comparison.

There are two separate out-of-sample questions here and only one of them
used to be answered. The SAMPLE is out-of-fold with respect to the model,
which is what keeps the test season clean. But the choice AMONG
calibrators was made in-sample on that sample - each candidate fitted on
it and then scored on it - which ranked methods by how many parameters
they had rather than by how well they generalised. Selection is now by
out-of-fold log loss within the sample too; see `select_calibration`.

Methods, in ascending order of flexibility - the ordering that used to
decide the winner on its own:
    none      identity                                        (0 params)
    platt     logistic recalibration on logit(p)              (2 params)
    beta      Beta calibration: logistic on [ln p, −ln(1−p)]  (3 params)
    isotonic  offered only when every training fold clears
              MIN_ISOTONIC_N; stored as knots               (~100 knots)
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


SELECTION_FOLDS = 5
SELECTION_SEED = 20260801


def _fold_indices(n: int, k: int) -> list[np.ndarray]:
    """A fixed, reproducible partition of 0..n-1 into k parts.

    Seeded rather than random: this partition decides which calibrator is
    written into a governed CalibrationArtifact, and a selection that
    changes between two runs over identical inputs is not a record.
    """
    order = np.random.default_rng(SELECTION_SEED).permutation(n)
    return [order[i::k] for i in range(k)]


def _out_of_fold_score(method: str, p: list[float], y: list[int], k: int) -> float:
    """Log loss of predictions each made by a fit that never saw them."""
    n = len(p)
    oof = np.zeros(n)
    for held_out in _fold_indices(n, k):
        mask = np.ones(n, dtype=bool)
        mask[held_out] = False
        train_p = [p[i] for i in range(n) if mask[i]]
        train_y = [y[i] for i in range(n) if mask[i]]
        fit = fit_calibration(method, train_p, train_y)
        for i in held_out:
            oof[i] = fit.apply(p[i])
    return log_loss(oof.tolist(), y)


def select_calibration(
    probs: list[float], outcomes: list[int], folds: int = SELECTION_FOLDS
) -> tuple[FittedCalibration, dict[str, float]]:
    """Choose a calibrator by OUT-OF-FOLD log loss, then refit it on all of
    the sample. Returns the winner plus every candidate's score (for the
    report — including the identity comparison).

    The scores used to be in-sample: each candidate was fitted on `probs`
    and then scored on `probs`. That does not compare calibrators, it ranks
    them by flexibility, because the more parameters a method has the more
    of its own fitting sample it can reproduce. `none` has none, platt has
    two, beta three, isotonic a hundred knots — so isotonic won whenever it
    was eligible, and on perfectly-calibrated input the identity method
    never won at all. Measured on held-out data the ordering inverts: at
    n=1600 the in-sample winner scored WORST of the four, and 0.016 of log
    loss worse than leaving the probabilities alone.

    A calibrator is the last thing standing between a model probability and
    a stated edge, so one selected for its ability to memorise a validation
    season is a direct route to overstated confidence.

    Each candidate is now scored on predictions made by fits that never saw
    the row being predicted. The winner is then refitted on the whole
    sample, which is standard: cross-validation chooses the METHOD, and the
    final artifact should still use all available data.
    """
    n = len(probs)
    candidates = ["none", "platt", "beta"]
    # Isotonic is offered only when every TRAINING fold clears the minimum,
    # not merely the full sample. Scoring it via fits below the threshold
    # it declares for itself would compare a method against a version of
    # itself the module says is unfit.
    smallest_train = n - -(-n // folds)  # n minus the largest fold
    if n >= MIN_ISOTONIC_N and smallest_train >= MIN_ISOTONIC_N:
        candidates.append("isotonic")

    scores = {m: _out_of_fold_score(m, probs, outcomes, folds) for m in candidates}
    best = min(scores, key=lambda m: scores[m])
    return fit_calibration(best, probs, outcomes), scores
