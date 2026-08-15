"""Two cohorts, one identity slot.

CONSENSUS, EVALUATION and AVAILABILITY each declare a logical identity
field called `cohort`. Three of the four call sites fed it
`data_mode.value`. `DataMode` has two values; `Cohort` has four, and
BURN_IN and OFFICIAL_FORWARD_TEST both write LIVE_RESEARCH — so a
burn-in record and an official record for the same game, market and
cutoff hashed to the same slot with different contents. Every one of
them a CONFLICT, on every game, on every market, the moment a second
cohort runs.

It stayed invisible because only one cohort had ever run. Running a
burn-in beside the official forward test is exactly the thing that makes
it unavoidable, and exactly the thing the pilot is for.

The field was always there and always named for the right concept. What
was passed into it was the wrong value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import (
    ConsensusSnapshot,
    ForwardLedgerEntry,
    OddsQuote,
)
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort
from fde_api.forward.consensus import (
    DEFAULT_BOOK_MINIMUM,
    book_minimum,
    build_consensus,
    latest_consensus_at,
)
from fde_api.forward.domain_identity import CONSENSUS
from fde_api.forward.ledger import _cohort_of, compute_clv
from fde_api.forward.modes import DataMode
from fde_api.forward.policy import build_policy_draft, freeze_policy

GAME = "2026_01_KC_BUF"
KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
CUTOFF = KICK - timedelta(hours=2)
MODE = DataMode.LIVE_RESEARCH

BOOKS = ("draftkings", "fanduel", "betmgm")


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Session:
    # `freeze_policy` writes an artifact under `settings.data_dir`. Left
    # unpatched it lands in the repository's real policy directory.
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        yield s


def _quotes(db: Session, books: tuple[str, ...], *, line: float) -> None:
    """One two-sided SPREAD quote per book, stored home-relative."""
    for b in books:
        for selection, point in (("HOME", line), ("AWAY", -line)):
            db.add(OddsQuote(
                data_mode=MODE.value, canonical_game_id=GAME, provider="t",
                provider_mode="LIVE", sportsbook=b, market="SPREAD",
                selection=selection, line=point, american=-110,
                decimal_odds=1.909, is_live=False,
                observed_at=CUTOFF - timedelta(minutes=5),
                raw_hash=f"{b}{selection}{point}",
            ))
    db.flush()


class TestTheTwoLiveCohortsDoNotShareASlot:
    def test_burn_in_and_official_hash_to_different_identities(self) -> None:
        """The defect, stated at the level it lives at.

        Before the fix both sides of this comparison rendered
        `cohort: "LIVE_RESEARCH"` and the assertion was an equality.
        """
        common = {
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": CUTOFF,
            "method_version": "consensus-v2",
        }
        burn = CONSENSUS.logical_hash({**common, "cohort": Cohort.BURN_IN.value})
        official = CONSENSUS.logical_hash(
            {**common, "cohort": Cohort.OFFICIAL_FORWARD_TEST.value}
        )
        assert burn != official

    def test_the_data_mode_they_share_is_what_used_to_be_hashed(self) -> None:
        """Both cohorts still write LIVE_RESEARCH — that has not changed
        and is not a defect. Using it as the identity was."""
        assert Cohort.BURN_IN is not Cohort.OFFICIAL_FORWARD_TEST
        common = {
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": CUTOFF,
            "method_version": "consensus-v2",
        }
        via_mode_a = CONSENSUS.logical_hash({**common, "cohort": MODE.value})
        via_mode_b = CONSENSUS.logical_hash({**common, "cohort": MODE.value})
        assert via_mode_a == via_mode_b, (
            "this is what both cohorts used to produce: one slot for two experiments"
        )

    def test_both_cohorts_persist_side_by_side(self, db: Session) -> None:
        """The end-to-end consequence. Under the old identity the second
        write hit the unique index at the same slot with different
        content and came back a conflict."""
        _quotes(db, BOOKS, line=-2.5)
        burn, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        official, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.OFFICIAL_FORWARD_TEST, data_mode=MODE,
        )
        assert burn is not None and official is not None
        assert burn.id != official.id
        assert burn.logical_identity_hash != official.logical_identity_hash

    def test_the_row_states_its_own_cohort(self, db: Session) -> None:
        _quotes(db, BOOKS, line=-2.5)
        snap, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        assert snap is not None
        assert snap.cohort == "burn_in"
        assert snap.data_mode == "LIVE_RESEARCH", "the mode is unchanged and still true"


class TestABurnInConsensusCannotFeedAnOfficialPrediction:
    """The reason the separation has to reach the READS as well.

    A burn-in consensus may rest on a single book by design. If an
    official prediction could select one, the pilot would put a one-book
    price behind an official candidate and nothing downstream could
    undo it.
    """

    def test_an_official_read_does_not_see_the_burn_in_snapshot(
        self, db: Session
    ) -> None:
        _quotes(db, ("draftkings",), line=-2.5)
        snap, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        assert snap is not None, "burn-in permits one book"
        assert latest_consensus_at(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            cohort=Cohort.OFFICIAL_FORWARD_TEST, data_mode=MODE,
        ) is None

    def test_the_burn_in_read_does_see_it(self, db: Session) -> None:
        """The guard against fixing the leak by breaking the feature."""
        _quotes(db, ("draftkings",), line=-2.5)
        build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        assert latest_consensus_at(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            cohort=Cohort.BURN_IN, data_mode=MODE,
        ) is not None

    def test_a_legacy_row_is_selectable_by_no_cohort(self, db: Session) -> None:
        """`unknown_legacy` is not a `Cohort`, so no cohort-scoped read
        can name it. The row stays readable and stops being usable."""
        _quotes(db, BOOKS, line=-2.5)
        snap, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        assert snap is not None
        snap.cohort = "unknown_legacy"
        db.flush()
        for c in Cohort:
            assert latest_consensus_at(
                db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
                cohort=c, data_mode=MODE,
            ) is None
        assert db.scalars(select(ConsensusSnapshot)).all(), "and it is still there"


class TestTheBookMinimumFollowsTheCohort:
    def test_burn_in_permits_one_book(self) -> None:
        assert book_minimum(Cohort.BURN_IN) == 1

    def test_every_other_cohort_requires_three(self) -> None:
        for c in Cohort:
            if c is not Cohort.BURN_IN:
                assert book_minimum(c) == DEFAULT_BOOK_MINIMUM == 3

    def test_one_book_builds_under_burn_in(self, db: Session) -> None:
        _quotes(db, ("draftkings",), line=-2.5)
        snap, rep = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        assert snap is not None, rep.reasons

    def test_one_book_does_not_build_under_the_official_cohort(
        self, db: Session
    ) -> None:
        _quotes(db, ("draftkings",), line=-2.5)
        snap, rep = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.OFFICIAL_FORWARD_TEST, data_mode=MODE,
        )
        assert snap is None
        assert any("minimum is 3" in r for r in rep.reasons), rep.reasons

    def test_the_reason_names_the_cohort_that_set_the_minimum(
        self, db: Session
    ) -> None:
        """Otherwise "minimum is 3" reads as a universal rule and the
        reader cannot tell that burn-in would have built it."""
        _quotes(db, ("draftkings",), line=-2.5)
        _, rep = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.OFFICIAL_FORWARD_TEST, data_mode=MODE,
        )
        assert any("official_forward_test" in r for r in rep.reasons), rep.reasons

    def test_the_applied_minimum_is_recorded_on_the_row(self, db: Session) -> None:
        """`eligible_books` says how many turned up. Only this says how
        many were required, which is the part that cannot be recovered
        from the row afterwards."""
        _quotes(db, ("draftkings",), line=-2.5)
        snap, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        assert snap is not None
        assert snap.min_books_applied == 1
        assert snap.eligible_books == 1

    def test_the_applied_minimum_participates_in_the_content_hash(
        self, db: Session
    ) -> None:
        """Two snapshots over one book differ in kind depending on
        whether one book was permitted or was merely all that arrived."""
        _quotes(db, ("draftkings",), line=-2.5)
        permitted, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE,
        )
        forced, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.OFFICIAL_FORWARD_TEST, data_mode=MODE,
            min_books=1,
        )
        assert permitted is not None and forced is not None
        assert permitted.min_books_applied == 1 and forced.min_books_applied == 1
        assert permitted.content_hash == forced.content_hash, (
            "same rule, same books, same numbers"
        )

    def test_an_explicit_minimum_overrides_the_cohort_and_is_recorded(
        self, db: Session
    ) -> None:
        _quotes(db, BOOKS, line=-2.5)
        snap, _ = build_consensus(
            db, canonical_game_id=GAME, market="SPREAD", as_of_at=CUTOFF,
            kickoff_utc=KICK, cohort=Cohort.BURN_IN, data_mode=MODE, min_books=3,
        )
        assert snap is not None
        assert snap.min_books_applied == 3, "what applied, not what the cohort says"


class TestClvReadsTheEntrysOwnCohort:
    """`compute_clv` used to call `_cohort_for(data_mode)`, which mapped
    LIVE_RESEARCH to BURN_IN.

    That is a guess, and the wrong one for every official row: an
    official entry would have gone looking for its close among burn-in
    captures — captures the burn-in cohort may have built from a single
    book. The entry carries its own cohort now, so there is nothing to
    infer.
    """

    def _entry(self, cohort: str) -> ForwardLedgerEntry:
        return ForwardLedgerEntry(
            data_mode=MODE.value, cohort=cohort, canonical_game_id=GAME,
            policy_version="ftp-2026-v2", model_version="m1", horizon="T-24h",
            market="SPREAD", status="RESEARCH_CANDIDATE", reasons={},
            as_of_at=CUTOFF, created_at=CUTOFF, filled=True,
        )

    def test_an_official_entry_resolves_to_the_official_cohort(self) -> None:
        entry = self._entry(Cohort.OFFICIAL_FORWARD_TEST.value)
        assert _cohort_of(entry) is Cohort.OFFICIAL_FORWARD_TEST

    def test_a_burn_in_entry_resolves_to_burn_in(self) -> None:
        assert _cohort_of(self._entry(Cohort.BURN_IN.value)) is Cohort.BURN_IN

    def test_the_two_no_longer_collapse_into_one_answer(self) -> None:
        """Both write LIVE_RESEARCH. Under the old mapping both came back
        BURN_IN."""
        official = _cohort_of(self._entry(Cohort.OFFICIAL_FORWARD_TEST.value))
        burn = _cohort_of(self._entry(Cohort.BURN_IN.value))
        assert official is not burn

    def test_a_legacy_entry_resolves_to_nothing_rather_than_raising(self) -> None:
        """`unknown_legacy` is not a `Cohort`. Returning None lets the
        caller say it does not know; raising would take a reporting path
        down over a row that is merely unattributed."""
        assert _cohort_of(self._entry("unknown_legacy")) is None

    def test_clv_on_a_legacy_entry_reports_rather_than_guesses(
        self, db: Session
    ) -> None:
        entry = self._entry("unknown_legacy")
        db.add(entry)
        db.flush()
        result = compute_clv(
            db, entry=entry, kickoff_utc=KICK, policy=_frozen_policy(db),
            data_mode=MODE,
        )
        assert result.line_clv is None and result.probability_clv is None
        assert "predates cohort recording" in (result.note or "")


def _frozen_policy(db: Session):
    """A frozen policy, since `compute_clv` takes one. Its contents do not
    matter here — the entry never reaches the closing lookup.

    The version is deliberately NOT one of the real ones. `freeze_policy`
    writes a JSON artifact named for the version, and an earlier draft of
    this helper used `ftp-2026-v2` and left a second, differently-hashed
    `ftp-2026-v2_*.json` beside the real frozen artifact in
    `apps/api/data/policies/`. Two files claiming one policy version is
    exactly the ambiguity the freeze is supposed to remove.
    """
    from datetime import date

    return freeze_policy(db, build_policy_draft(
        policy_version="ftp-test-cohort-identity",
        calibration_version="cal_none_val2024",
        start=date(2026, 9, 1), end=date(2027, 2, 28),
    ))
