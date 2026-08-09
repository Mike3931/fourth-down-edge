"""How a calibrator is CHOSEN, as distinct from how one is fitted.

Every candidate used to be fitted on the sample and then scored on that
same sample. That does not compare calibrators; it ranks them by
flexibility. `none` carries no parameters, platt two, beta three, and
isotonic a hundred knots, so the score was very nearly a count of how much
of its own fitting data each method could reproduce.

The consequence was not academic. On input that was ALREADY well
calibrated — where the right answer is to leave it alone — the identity
method never won, and isotonic won whenever it was eligible. Measured on
held-out data at n=1600 the in-sample winner scored worst of the four, and
0.016 of log loss worse than doing nothing at all.

A calibrator is the last thing between a model probability and a stated
edge. One selected for its ability to memorise a validation season is a
direct route to overstated confidence, which is the failure this whole
system is built to avoid.

Selection is now by out-of-fold log loss. These tests assert the property
that fixes, not the particular method chosen for any one sample — the
choice legitimately depends on the data, and pinning it would make the
test a snapshot rather than a statement.
"""

from __future__ import annotations

import numpy as np
import pytest

from fde_api.calibration import (
    MIN_ISOTONIC_N,
    SELECTION_FOLDS,
    fit_calibration,
    select_calibration,
)
from fde_api.evaluation import log_loss


def _already_calibrated(n: int, seed: int) -> tuple[list[float], list[int]]:
    """Probabilities that need no correction at all.

    The honest answer for this sample is `none`. Any method that beats it
    is beating it on noise, which is precisely what in-sample scoring
    rewarded.
    """
    rng = np.random.default_rng(seed)
    p = rng.beta(5, 5, size=n)  # concentrated near 0.5, like spread covers
    y = (rng.random(n) < p).astype(int)
    return list(p), [int(v) for v in y]


class TestSelectionIsNotScoredOnItsOwnFit:
    def test_a_perfectly_separable_sample_does_not_score_zero(self) -> None:
        """The tell that exposed this.

        Fitted and scored on the same rows, platt reached a log loss of
        EXACTLY 0.0 on separable input - a perfect score, reported as
        evidence that the calibrator was good. Out of fold it cannot,
        because it must predict rows it never saw.
        """
        probs = [0.2, 0.3, 0.4, 0.45, 0.55, 0.6, 0.7, 0.8] * 6
        outcomes = [0 if p < 0.5 else 1 for p in probs]
        _, scores = select_calibration(probs, outcomes)
        assert all(s > 0.0 for s in scores.values()), scores

    def test_the_identity_method_can_win(self) -> None:
        """Under in-sample scoring `none` could essentially never win: any
        fitted method reproduces its own sample at least as well. On input
        that needs no correction it must be able to."""
        winners = {
            select_calibration(*_already_calibrated(600, seed)).__getitem__(0).method
            for seed in range(6)
        }
        assert "none" in winners, f"identity never selected across six samples: {winners}"

    def test_flexibility_alone_does_not_win(self) -> None:
        """Isotonic has ~100 knots against platt's 2. On already-calibrated
        data it should not be preferred merely for being able to bend."""
        n = MIN_ISOTONIC_N * 2
        chosen, scores = select_calibration(*_already_calibrated(n, seed=11))
        assert "isotonic" in scores, "the eligibility gate should have offered it"
        assert chosen.method != "isotonic", (
            "isotonic won on data that needed no calibration, which is the "
            "flexibility bias returning"
        )


class TestTheChosenCalibratorGeneralises:
    @pytest.mark.parametrize("seed", [3, 17, 29])
    def test_it_is_never_the_worst_candidate_on_held_out_data(self, seed: int) -> None:
        """The concrete harm: in-sample selection picked the WORST of four
        on held-out data at n=900 and n=1600. Being optimal every time is
        not achievable from a finite sample, but being worst is a signal
        that selection is measuring the wrong thing."""
        fit_p, fit_y = _already_calibrated(1200, seed)
        ho_p, ho_y = _already_calibrated(4000, seed + 500)
        chosen, _ = select_calibration(fit_p, fit_y)

        held_out: dict[str, float] = {}
        for method in ("none", "platt", "beta", "isotonic"):
            try:
                fit = fit_calibration(method, fit_p, fit_y)
            except ValueError:
                continue  # isotonic below its minimum
            held_out[method] = log_loss([fit.apply(p) for p in ho_p], ho_y)

        worst = max(held_out, key=lambda m: held_out[m])
        assert chosen.method != worst, (
            f"selected {chosen.method}, the worst held-out candidate: {held_out}"
        )


class TestTheRecordIsReproducible:
    def test_two_runs_over_identical_input_select_identically(self) -> None:
        """The choice is written into a governed CalibrationArtifact. A
        selection that moves between runs over the same data is not a
        record, so the fold partition is seeded rather than random."""
        sample = _already_calibrated(700, seed=5)
        first, first_scores = select_calibration(*sample)
        second, second_scores = select_calibration(*sample)
        assert first.method == second.method
        assert first_scores == second_scores

    def test_the_winner_is_refitted_on_the_whole_sample(self) -> None:
        """Cross-validation chooses the METHOD; the stored artifact should
        still use every row available to it."""
        probs, outcomes = _already_calibrated(600, seed=7)
        chosen, _ = select_calibration(probs, outcomes)
        assert chosen.sample_size == len(probs)


class TestTheIsotonicGateCoversTheFoldsToo:
    def test_it_is_withheld_when_a_training_fold_would_be_too_small(self) -> None:
        """A sample just over the threshold leaves training folds under it.
        Scoring isotonic there would compare it against a version of itself
        the module declares unfit."""
        n = MIN_ISOTONIC_N + 10  # folds train on ~80% of this, below the minimum
        _, scores = select_calibration(*_already_calibrated(n, seed=2))
        assert "isotonic" not in scores

    def test_it_is_offered_once_every_fold_clears_the_minimum(self) -> None:
        n = int(MIN_ISOTONIC_N * SELECTION_FOLDS / (SELECTION_FOLDS - 1)) + 50
        _, scores = select_calibration(*_already_calibrated(n, seed=2))
        assert "isotonic" in scores

    def test_it_is_never_offered_on_a_small_sample(self) -> None:
        _, scores = select_calibration(*_already_calibrated(100, seed=2))
        assert "isotonic" not in scores
