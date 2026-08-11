"""Whether a policy is frozen is a question for the database.

`policy_hash_integrity` is CRITICAL and sits in GOVERNANCE_INTEGRITY, so
failing it suppresses candidate generation. It used to branch on the
`policy_version` PARAMETER passed to `run_health_checks`:

    if policy_version:   ...verify hashes...
    else:                "no forward-test policy in force"

Neither endpoint passes that parameter. So the Data Health screen showed a
permanent CRITICAL blocker telling the operator to "freeze a policy before
capture" while `ftp-2026-v1` sat frozen, hash-intact, on disk and in the
database. The check reported the caller's arguments, not the system.

Three states now, because they call for three different actions:

  * nothing frozen            -> a real blocker
  * frozen, window not open   -> normal before a season starts; blocks nothing
  * frozen and in force       -> hashes verified

The middle one is the state this repository is actually in for most of the
year, and reporting it as a fault is how a real blocker gets ignored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.models import Base
from fde_api.forward.health import run_health_checks
from fde_api.forward.modes import DataMode
from fde_api.forward.policy import build_policy_draft, freeze_policy

WINDOW_START = date(2026, 9, 1)
WINDOW_END = date(2027, 2, 28)
BEFORE_WINDOW = datetime(2026, 8, 10, tzinfo=UTC)
INSIDE_WINDOW = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture()
def session(tmp_path, monkeypatch) -> Session:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _freeze(session: Session, version: str = "ftp-2026-v1") -> None:
    freeze_policy(session, build_policy_draft(
        policy_version=version, start=WINDOW_START, end=WINDOW_END,
    ))
    session.commit()


def _policy_check(session: Session, now: datetime) -> dict:
    report = run_health_checks(session, now=now, data_mode=DataMode.LIVE_RESEARCH)
    return next(c for c in report["checks"] if c["id"] == "policy_hash_integrity")


class TestTheCheckAsksTheDatabase:
    def test_no_policy_frozen_is_a_real_blocker(self, session: Session) -> None:
        check = _policy_check(session, BEFORE_WINDOW)
        assert check["status"] != "OK"
        assert "no forward-test policy has been frozen" in check["explanation"]

    def test_a_frozen_policy_passes_without_the_caller_naming_it(
        self, session: Session
    ) -> None:
        """The defect: `run_health_checks` is called with no policy_version
        by both endpoints, and that alone used to fail the check."""
        _freeze(session)
        check = _policy_check(session, INSIDE_WINDOW)
        assert check["status"] == "OK", check["explanation"]
        assert "ftp-2026-v1 in force" in check["explanation"]

    def test_frozen_but_not_yet_in_force_is_not_a_fault(
        self, session: Session
    ) -> None:
        """Before the season opens, nothing is wrong and nothing needs
        doing. Reporting it as CRITICAL is how a real blocker stops being
        read."""
        _freeze(session)
        check = _policy_check(session, BEFORE_WINDOW)
        assert check["status"] == "OK", check["explanation"]
        assert "none in force today" in check["explanation"]
        # The window is stated, so the reader knows when it opens.
        assert "2026-09-01" in check["explanation"]
        assert "2027-02-28" in check["explanation"]


class TestItStillBlocksWhenItShould:
    def test_a_tampered_policy_fails_verification(self, session: Session) -> None:
        """The check's real job. Altering the stored payload must break the
        recorded hash and fail CRITICAL."""
        from fde_api.db.forward_models import ForwardTestPolicyRecord

        _freeze(session)
        rec = session.get(ForwardTestPolicyRecord, "ftp-2026-v1")
        assert rec is not None
        payload = dict(rec.payload)
        payload["research_candidate_edge_threshold"] = 0.001  # a rule changed
        rec.payload = payload
        session.commit()

        check = _policy_check(session, INSIDE_WINDOW)
        assert check["status"] != "OK"
        assert "fail hash verification" in check["explanation"]

    def test_it_suppresses_candidates_when_it_fails(self, session: Session) -> None:
        """Governance integrity blocks the affected operation; that is the
        whole reason a false failure here mattered."""
        check = _policy_check(session, BEFORE_WINDOW)
        assert check["suppresses_candidates"] is True
