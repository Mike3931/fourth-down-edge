"""Generated keys must fit the column they are stored in.

The defect this pins: `make_idempotency_key` inlined the params dict into
the key, so the key was as long as the payload. `scheduled_job_runs
.idempotency_key` is VARCHAR(160). SQLite does not enforce VARCHAR length -
it stores whatever it is given - so the entire SQLite suite passed while
PostgreSQL rejected the insert outright:

    psycopg.errors.StringDataRightTruncation:
        value too long for type character varying(160)

It surfaced only when a real PostgreSQL race gate ran in CI. That is the
general shape of the risk, so these tests derive the limit from the ORM
metadata rather than restating it: if the column is ever narrowed, the
generator's bound is re-checked automatically instead of drifting.

These run on SQLite deliberately. The bound is a property of the generated
string, not of the backend, and pinning it here means the next over-long
key is caught in the fast suite rather than in the PostgreSQL gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import String

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.db.models import Team
from fde_api.forward.cohort import Cohort
from fde_api.forward.recovery import (
    BASE_KEY_MAX,
    IDEMPOTENCY_KEY_MAX,
    RECOVERY_SUFFIX_MAX,
    RecoveryError,
    RecoveryKey,
    bound_key,
    build_recovery_key,
)
from fde_api.forward.scheduler import make_idempotency_key

SLOT = datetime(2026, 9, 10, 17, 0, tzinfo=UTC)


def _column_length(name: str) -> int:
    col = ScheduledJobRun.__table__.c[name]
    assert isinstance(col.type, String), f"{name} is {col.type!r}, not a bounded String"
    assert col.type.length, f"{name} has no declared length"
    return col.type.length


class TestTheDeclaredBoundMatchesTheSchema:
    def test_the_constant_tracks_the_actual_column(self) -> None:
        """If the column is resized, the generator's bound must be resized
        with it - otherwise this file certifies a limit nothing enforces."""
        assert _column_length("idempotency_key") == IDEMPOTENCY_KEY_MAX

    def test_the_original_key_column_is_the_same_width(self) -> None:
        """`original_idempotency_key` holds a base key verbatim."""
        assert _column_length("original_idempotency_key") >= BASE_KEY_MAX

    def test_the_recovery_suffix_budget_covers_the_run_id_column(self) -> None:
        """The suffix carries a root_run_id, so its worst case is that
        column's full width. A budget smaller than the column would fit in
        testing and overflow on a long id."""
        assert _column_length("root_run_id") <= RECOVERY_SUFFIX_MAX

    def test_base_and_suffix_budgets_sum_within_the_column(self) -> None:
        assert BASE_KEY_MAX + RECOVERY_SUFFIX_MAX <= IDEMPOTENCY_KEY_MAX
        assert BASE_KEY_MAX > 0


class TestGeneratedKeysAreBounded:
    def test_a_plain_key_fits(self) -> None:
        key = make_idempotency_key(job_name="odds_capture", slot=SLOT, cohort=Cohort.BURN_IN)
        assert len(key) <= BASE_KEY_MAX, (len(key), key)

    def test_a_large_params_payload_still_fits(self) -> None:
        """The regression itself. A fixture odds payload ran to several
        hundred characters; inlined, it produced a key PostgreSQL refused."""
        payload = {"fixture_payload": [{"game": f"g{i}", "books": ["a"] * 20} for i in range(50)]}
        key = make_idempotency_key(
            job_name="odds_capture", slot=SLOT, cohort=Cohort.BURN_IN, params=payload
        )
        assert len(key) <= BASE_KEY_MAX, (len(key), key[:80])

    @pytest.mark.parametrize("size", [1, 10, 100, 1000, 10_000])
    def test_the_key_length_does_not_grow_with_the_payload(self, size: int) -> None:
        key = make_idempotency_key(
            job_name="odds_capture", slot=SLOT, cohort=Cohort.BURN_IN,
            params={"blob": "x" * size},
        )
        assert len(key) <= BASE_KEY_MAX, (size, len(key))

    def test_a_very_long_job_name_is_bounded_too(self) -> None:
        key = make_idempotency_key(job_name="j" * 500, slot=SLOT, cohort=Cohort.BURN_IN)
        assert len(key) <= BASE_KEY_MAX, len(key)


class TestBoundingPreservesIdentity:
    def test_different_params_produce_different_keys(self) -> None:
        a = make_idempotency_key(job_name="j", slot=SLOT, cohort=Cohort.BURN_IN,
                                 params={"blob": "x" * 5000, "n": 1})
        b = make_idempotency_key(job_name="j", slot=SLOT, cohort=Cohort.BURN_IN,
                                 params={"blob": "x" * 5000, "n": 2})
        assert a != b, "bounding collapsed two distinct payloads onto one key"

    def test_the_same_params_produce_the_same_key(self) -> None:
        """Idempotency depends on this: a re-run at the same slot with the
        same params must recognise itself."""
        p = {"b": [1, 2, 3], "a": "z"}
        first = make_idempotency_key(job_name="j", slot=SLOT, cohort=Cohort.BURN_IN, params=p)
        second = make_idempotency_key(job_name="j", slot=SLOT, cohort=Cohort.BURN_IN,
                                      params={"a": "z", "b": [1, 2, 3]})
        assert first == second, "key depends on dict ordering"

    def test_params_absent_and_params_empty_agree(self) -> None:
        none_key = make_idempotency_key(job_name="j", slot=SLOT, cohort=Cohort.BURN_IN)
        empty_key = make_idempotency_key(job_name="j", slot=SLOT, cohort=Cohort.BURN_IN, params={})
        assert none_key == empty_key

    def test_a_short_key_passes_through_unchanged(self) -> None:
        """Historical rows must keep matching; only over-long keys change."""
        assert bound_key("odds_capture:burn_in:20260910T170000Z") == (
            "odds_capture:burn_in:20260910T170000Z"
        )

    def test_a_bounded_key_is_not_mistaken_for_a_recovery_key(self) -> None:
        bounded = bound_key("j" * 400)
        original, root, seq = RecoveryKey.parse(bounded)
        assert original == bounded
        assert root is None
        assert seq == 0


class TestTheWidthGuardIsLoadBearing:
    """The conftest listener that makes SQLite behave like PostgreSQL.

    An inert guard is worse than no guard - it advertises a protection that
    is not there. These prove it actually fires, and that it does not fire
    on values that fit.
    """

    def test_an_over_long_value_is_refused_on_insert(self, session) -> None:
        from conftest import StringWidthExceeded

        session.add(Team(id="X" * 40, name="too wide"))
        with pytest.raises(StringWidthExceeded, match="VARCHAR"):
            session.flush()

    def test_the_message_names_the_column_and_the_limit(self, session) -> None:
        from conftest import StringWidthExceeded

        session.add(Team(id="X" * 40, name="too wide"))
        with pytest.raises(StringWidthExceeded) as e:
            session.flush()
        assert "teams.id" in str(e.value)
        assert "VARCHAR(8)" in str(e.value)
        assert "40 characters" in str(e.value)

    def test_a_value_that_fits_is_untouched(self, session) -> None:
        session.add(Team(id="KC", name="Kansas City"))
        session.flush()
        assert session.get(Team, "KC") is not None

    def test_an_over_long_update_is_refused_too(self, session) -> None:
        from conftest import StringWidthExceeded

        session.add(Team(id="KC", name="Kansas City"))
        session.flush()
        team = session.get(Team, "KC")
        assert team is not None
        team.name = "N" * 200
        with pytest.raises(StringWidthExceeded, match="teams.name"):
            session.flush()


class TestRecoveryKeysFitTheColumn:
    def test_the_worst_case_chain_fits(self) -> None:
        base = make_idempotency_key(
            job_name="j" * 300, slot=SLOT, cohort=Cohort.BURN_IN, params={"blob": "x" * 9000}
        )
        for seq in (1, 9, 99, 999, 999_999):
            key = build_recovery_key(
                job_name="j", cohort="burn_in", logical_slot=SLOT.isoformat(),
                root_run_id="r" * _column_length("root_run_id"),
                sequence=seq, original_key=base,
            )
            assert len(key) <= IDEMPOTENCY_KEY_MAX, (seq, len(key))

    def test_an_unbounded_original_is_refused_not_truncated(self) -> None:
        """Silently truncating would merge two attempts onto one unique key,
        so the second would look already-done. Refusing is the safe failure."""
        with pytest.raises(RecoveryError, match="exceeding"):
            build_recovery_key(
                job_name="j", cohort="c", logical_slot="s", root_run_id="r",
                sequence=1, original_key="x" * IDEMPOTENCY_KEY_MAX,
            )

    def test_an_over_wide_sequence_is_refused(self) -> None:
        with pytest.raises(RecoveryError, match="digits"):
            build_recovery_key(
                job_name="j", cohort="c", logical_slot="s", root_run_id="r",
                sequence=10_000_000, original_key="orig",
            )

    def test_sequence_zero_is_still_the_base_key(self) -> None:
        base = make_idempotency_key(job_name="odds_capture", slot=SLOT, cohort=Cohort.BURN_IN)
        assert build_recovery_key(
            job_name="odds_capture", cohort="burn_in", logical_slot=SLOT.isoformat(),
            root_run_id="run_a", sequence=0, original_key=base,
        ) == base
