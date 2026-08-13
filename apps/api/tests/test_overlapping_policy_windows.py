"""Two frozen policies can cover one date, and the reader must be told.

Pinning a calibration artifact onto a frozen policy is not an edit —
`freeze_policy` refuses to alter a frozen record, by design — so it takes a
NEW policy version. `ftp-2026-v2` therefore carries the same window as
`ftp-2026-v1`, 2026-09-01 to 2027-02-28, and both claim to govern every
game in it.

`active_policy` resolves that by `created_at DESC`: the newest frozen
policy covering the date wins. Deterministic, and the right rule — a later
policy is the later decision. But it was resolved SILENTLY. Data Health
said "ftp-2026-v2 in force" and a reader had no way to know that a second
frozen policy also claimed the window, or that recency was what settled
it.

That is the shape of the defect this codebase keeps finding: an ambiguity
answered correctly, and the answer presented as though there had been no
question.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.models import Base
from fde_api.forward.policy import active_policy, build_policy_draft, freeze_policy

WINDOW = (date(2026, 9, 1), date(2027, 2, 28))
IN_WINDOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Session:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


def _freeze(db: Session, version: str, calibration: str | None) -> None:
    freeze_policy(db, build_policy_draft(
        policy_version=version, calibration_version=calibration,
        start=WINDOW[0], end=WINDOW[1],
    ))
    db.flush()


def _policy_check(db: Session, now: datetime):
    from fde_api.forward.health import run_health_checks

    for c in run_health_checks(db, now=now)["checks"]:
        if c["id"] == "policy_hash_integrity":
            return c
    raise AssertionError("policy_hash_integrity")


class TestTheNewerPolicyWins:
    def test_the_most_recently_frozen_covering_policy_is_in_force(
        self, db: Session
    ) -> None:
        _freeze(db, "ftp-2026-v1", None)
        _freeze(db, "ftp-2026-v2", "cal_none_val2024")
        policy = active_policy(db, at=IN_WINDOW.date())
        assert policy is not None and policy.policy_version == "ftp-2026-v2"

    def test_the_winner_carries_the_pinned_calibration(self, db: Session) -> None:
        """The whole reason for the second version. `null` could mean
        "identity was selected" or "nobody decided"; the artifact id can
        only mean the first."""
        _freeze(db, "ftp-2026-v1", None)
        _freeze(db, "ftp-2026-v2", "cal_none_val2024")
        policy = active_policy(db, at=IN_WINDOW.date())
        assert policy is not None
        assert policy.calibration_version == "cal_none_val2024"

    def test_a_single_policy_is_still_chosen_normally(self, db: Session) -> None:
        _freeze(db, "ftp-2026-v1", None)
        policy = active_policy(db, at=IN_WINDOW.date())
        assert policy is not None and policy.policy_version == "ftp-2026-v1"

    def test_a_date_outside_every_window_still_returns_nothing(
        self, db: Session
    ) -> None:
        _freeze(db, "ftp-2026-v1", None)
        _freeze(db, "ftp-2026-v2", "cal_none_val2024")
        assert active_policy(db, at=date(2026, 8, 13)) is None


class TestHealthSaysThereWasAChoice:
    def test_it_names_the_other_policy_that_claims_the_window(
        self, db: Session
    ) -> None:
        """Reporting only the winner hides that anything was resolved."""
        _freeze(db, "ftp-2026-v1", None)
        _freeze(db, "ftp-2026-v2", "cal_none_val2024")
        explanation = _policy_check(db, IN_WINDOW)["explanation"]
        assert "ftp-2026-v2" in explanation
        assert "ftp-2026-v1" in explanation, "the superseded policy is not named"

    def test_it_says_recency_is_what_settled_it(self, db: Session) -> None:
        _freeze(db, "ftp-2026-v1", None)
        _freeze(db, "ftp-2026-v2", "cal_none_val2024")
        explanation = _policy_check(db, IN_WINDOW)["explanation"].lower()
        assert "supersed" in explanation or "most recently frozen" in explanation

    def test_one_policy_reads_plainly_with_no_talk_of_a_choice(
        self, db: Session
    ) -> None:
        """The guard against noise: with nothing to resolve, say nothing
        about resolving."""
        _freeze(db, "ftp-2026-v1", None)
        explanation = _policy_check(db, IN_WINDOW)["explanation"]
        assert "ftp-2026-v1 in force" in explanation
        assert "supersed" not in explanation.lower()

    def test_the_check_still_passes_because_an_overlap_is_not_a_fault(
        self, db: Session
    ) -> None:
        """Superseding a policy is the documented way to change a rule.
        Naming it is reporting, not alarming."""
        from fde_api.forward.health import Status

        _freeze(db, "ftp-2026-v1", None)
        _freeze(db, "ftp-2026-v2", "cal_none_val2024")
        assert _policy_check(db, IN_WINDOW)["status"] == Status.OK.value
