"""A retry suffix must fit the column it is written to.

`recovery.py` sets up an arithmetic budget for idempotency keys and says
what it is for: a base key is capped at `BASE_KEY_MAX` so that the
worst-case RECOVERY suffix still fits `VARCHAR(160)`, making "every key in
a chain fit by construction rather than by luck".

The budget accounted for the recovery suffix and not for the RETRY suffix.
`run_job` appends `#r{attempt}` on each failed attempt, cumulatively, and
never re-bounded the result. A worst-case recovery key is exactly 160
characters — the budget is deliberately tight — so a recovery that then
failed and retried produced 163, 166 and 169.

That is the same overflow `make_idempotency_key` exists to prevent, and it
fails the same way: SQLite does not enforce VARCHAR length, so every local
test passes, while PostgreSQL raises StringDataRightTruncation. The bug
would surface only in the real database, only on a retried recovery of a
maximal key — which is to say, in production and not before.

These tests are arithmetic rather than database-backed on purpose. Driving
a real retried recovery to a maximal key needs a great deal of setup, and
the property is about lengths, which can be asserted directly.
"""

from __future__ import annotations

import pytest

from fde_api.forward.recovery import (
    _RUN_ID_MAX,
    BASE_KEY_MAX,
    IDEMPOTENCY_KEY_MAX,
    bound_key,
    build_recovery_key,
)


# `run_job` retries with `key = bound_key(f"{key}#r{attempt}", IDEMPOTENCY_KEY_MAX)`.
def _retry(key: str, attempt: int) -> str:
    return bound_key(f"{key}#r{attempt}", IDEMPOTENCY_KEY_MAX)


def _worst_case_recovery_key() -> str:
    """Exactly what the budget permits: base at its cap, run id at its cap,
    sequence at its digit limit."""
    return build_recovery_key(
        job_name="j", cohort="c", logical_slot="s",
        root_run_id="r" * _RUN_ID_MAX,
        sequence=999999,
        original_key=bound_key("x" * 500),
    )


class TestTheBudgetIsActuallyTight:
    def test_a_worst_case_recovery_key_fills_the_column_exactly(self) -> None:
        """If this stops being true the tests below stop testing anything,
        because the overflow they guard depends on the budget being tight."""
        assert len(_worst_case_recovery_key()) == IDEMPOTENCY_KEY_MAX


class TestRetryingNeverOverflowsTheColumn:
    def test_a_retried_worst_case_recovery_key_still_fits(self) -> None:
        """The defect: 160 + '#r1' = 163, and it kept growing."""
        key = _worst_case_recovery_key()
        for attempt in range(1, 6):
            key = _retry(key, attempt)
            assert len(key) <= IDEMPOTENCY_KEY_MAX, f"attempt {attempt}: {len(key)}"

    def test_an_ordinary_key_is_unchanged_by_bounding(self) -> None:
        """Short keys must pass through byte-for-byte, or historical rows
        stop matching and every idempotency check breaks."""
        key = bound_key("odds_capture:BURN_IN:20260913T170000Z")
        assert _retry(key, 1) == f"{key}#r1"
        assert _retry(f"{key}#r1", 2) == f"{key}#r1#r2"

    def test_retries_of_one_key_stay_distinct(self) -> None:
        """Distinctness is the whole point of the suffix: each attempt is
        its own row, preserved as history. Bounding must not collapse
        two attempts onto one key."""
        key = _worst_case_recovery_key()
        seen = {key}
        for attempt in range(1, 6):
            key = _retry(key, attempt)
            assert key not in seen, f"attempt {attempt} collided with an earlier key"
            seen.add(key)

    @pytest.mark.parametrize("base_len", [10, 40, BASE_KEY_MAX, BASE_KEY_MAX + 40])
    def test_it_holds_across_base_lengths(self, base_len: int) -> None:
        key = bound_key("k" * base_len)
        for attempt in range(1, 5):
            key = _retry(key, attempt)
            assert len(key) <= IDEMPOTENCY_KEY_MAX
