"""Logical identity, content, and the three outcomes — sequentially.

The property under test is the distinction that makes retries and
contradictions separable:

    same slot + same content  -> EXISTING_IDENTICAL, no new row
    same slot + diff content  -> CONFLICT, original untouched
    different slot            -> CREATED, both rows retained

The concurrent half of this lives in the PostgreSQL race gate. These are
the sequential guarantees, which are what a caller sees in the ordinary
case and which must hold before contention is worth testing at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import (
    AvailabilityAssessment,
    ConsensusSnapshot,
    ManualBookPriceEntry,
    OddsQuote,
)
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.domain_identity import (
    ALL_IDENTITIES,
    AVAILABILITY,
    BY_ENTITY,
    CONSENSUS,
    CONTENT_HASH_VERSION,
    LOGICAL_IDENTITY_VERSION,
    PRICE_OBSERVATION,
    IdentityError,
    IdentityOutcome,
    UnknownHashVersion,
    canonical_payload,
    require_known_versions,
    specification,
    upsert_by_identity,
)
from fde_api.forward.modes import DataMode

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
CUTOFF = KICK - timedelta(days=1)
GAME = "2026_02_KC_BUF"
MODE = DataMode.DEMO
SPEC_DOC = Path(__file__).resolve().parents[3] / "docs" / "domain-identity.md"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


# --------------------------------------------------------------------------- #
# §3 — canonicalisation
# --------------------------------------------------------------------------- #


class TestCanonicalisation:
    def test_key_order_does_not_change_the_hash(self) -> None:
        a = CONSENSUS.logical_hash({
            "canonical_game_id": GAME, "market": "SPREAD", "cutoff": CUTOFF,
            "cohort": "DEMO", "method_version": "v1",
        })
        b = CONSENSUS.logical_hash({
            "method_version": "v1", "cohort": "DEMO", "cutoff": CUTOFF,
            "market": "SPREAD", "canonical_game_id": GAME,
        })
        assert a == b

    def test_list_order_matters_where_it_is_semantic(self) -> None:
        """A list is ordered content; a SET is not. Both are rendered
        deterministically, but only the set is reordered."""
        one = CONSENSUS.content_hash({"quote_lineage": {"ids": [1, 2, 3]}})
        two = CONSENSUS.content_hash({"quote_lineage": {"ids": [3, 2, 1]}})
        assert one != two
        assert (CONSENSUS.content_hash({"quote_lineage": {1, 2, 3}})
                == CONSENSUS.content_hash({"quote_lineage": {3, 1, 2}}))

    def test_a_naive_timestamp_is_refused(self) -> None:
        """Identity depends on an unambiguous instant. A naive value would
        hash the same as an aware one at a different real moment."""
        with pytest.raises(IdentityError, match="naive timestamp"):
            CONSENSUS.logical_hash({"cutoff": datetime(2026, 9, 12, 17, 0)})

    def test_equivalent_instants_in_different_zones_agree(self) -> None:
        from datetime import timezone

        utc = CONSENSUS.logical_hash({"cutoff": CUTOFF})
        offset = CONSENSUS.logical_hash({
            "cutoff": CUTOFF.astimezone(timezone(timedelta(hours=5)))})
        assert utc == offset

    def test_float_noise_does_not_change_the_hash(self) -> None:
        a = CONSENSUS.content_hash({"median_line": -3.5})
        b = CONSENSUS.content_hash({"median_line": -3.5 + 1e-12})
        assert a == b

    def test_missing_is_distinct_from_null(self) -> None:
        """Collapsing them would make a record that never carried a value
        hash the same as one explicitly carrying none."""
        absent = CONSENSUS.content_hash({})
        explicit_null = CONSENSUS.content_hash({"median_line": None})
        assert absent != explicit_null

    def test_a_field_outside_the_allowlist_is_refused(self) -> None:
        """A new column must not silently join an identity: that would
        change every stored hash without anyone deciding it should."""
        with pytest.raises(IdentityError, match="not in the allowlist"):
            canonical_payload({"surprise": 1}, CONSENSUS.logical_fields)

    def test_an_unknown_hash_version_is_refused(self) -> None:
        with pytest.raises(UnknownHashVersion):
            require_known_versions("some-other-version", CONTENT_HASH_VERSION)


# --------------------------------------------------------------------------- #
# §8 — the specification matches the code
# --------------------------------------------------------------------------- #


class TestTheSpecificationMatchesTheImplementation:
    def test_the_document_exists(self) -> None:
        assert SPEC_DOC.exists(), f"{SPEC_DOC} is missing"

    @pytest.mark.parametrize("identity", ALL_IDENTITIES, ids=lambda i: i.entity)
    def test_every_field_appears_in_the_document(self, identity) -> None:
        """Documentation that has drifted from the code is worse than none:
        it is read and believed."""
        doc = SPEC_DOC.read_text(encoding="utf-8")
        missing = [f for f in identity.logical_fields + identity.content_fields
                   if f"`{f}`" not in doc]
        assert not missing, f"{identity.entity}: undocumented fields {missing}"

    @pytest.mark.parametrize("identity", ALL_IDENTITIES, ids=lambda i: i.entity)
    def test_the_entity_is_named_with_its_versions(self, identity) -> None:
        doc = SPEC_DOC.read_text(encoding="utf-8")
        assert identity.entity in doc
        assert LOGICAL_IDENTITY_VERSION in doc
        assert CONTENT_HASH_VERSION in doc

    def test_the_specification_is_machine_readable(self) -> None:
        spec = specification()
        assert spec["digest"] == "sha256"
        assert len(spec["entities"]) == 4
        assert {e["entity"] for e in spec["entities"]} == set(BY_ENTITY)
        json.dumps(spec)  # must be serialisable for the audit artifact

    def test_no_identity_includes_a_database_key(self) -> None:
        for identity in ALL_IDENTITIES:
            fields = identity.logical_fields + identity.content_fields
            assert "id" not in fields
            for f in fields:
                assert not f.endswith("_id") or f in {
                    "canonical_game_id", "player_id", "prediction_identity",
                }, f"{identity.entity}: {f} looks like a database key"


# --------------------------------------------------------------------------- #
# §14 — sequential behaviour, per service
# --------------------------------------------------------------------------- #


def _quotes(db: Session, *, at: datetime, total: float) -> None:
    for book in ("draftkings", "fanduel", "betmgm"):
        for selection in ("OVER", "UNDER"):
            db.add(OddsQuote(
                data_mode=MODE.value, canonical_game_id=GAME, provider="fixture",
                provider_mode=ProviderMode.FIXTURE.value, sportsbook=book,
                market="TOTAL", selection=selection, line=total, american=-110,
                decimal_odds=1.909, observed_at=at, provider_timestamp=at,
                raw_hash=f"{book}{selection}{at.isoformat()}{total}",
            ))
    db.commit()


class TestConsensusIdentity:
    def _build(self, db: Session, *, at: datetime):
        from fde_api.forward.consensus import build_consensus

        return build_consensus(db, canonical_game_id=GAME, market="TOTAL",
                               as_of_at=at, kickoff_utc=KICK, data_mode=MODE)

    def test_first_insertion_creates_one_row(self, db: Session) -> None:
        _quotes(db, at=CUTOFF - timedelta(hours=1), total=47.5)
        snap, rep = self._build(db, at=CUTOFF)
        assert snap is not None
        assert snap.logical_identity_version == LOGICAL_IDENTITY_VERSION
        assert snap.content_hash_version == CONTENT_HASH_VERSION
        assert len(snap.logical_identity_hash) == 64
        assert any("CREATED" in r for r in rep.reasons)

    def test_exact_retry_adds_no_row(self, db: Session) -> None:
        _quotes(db, at=CUTOFF - timedelta(hours=1), total=47.5)
        first, _ = self._build(db, at=CUTOFF)
        second, rep = self._build(db, at=CUTOFF)
        assert second is not None and first is not None
        assert second.id == first.id
        assert any("EXISTING_IDENTICAL" in r for r in rep.reasons)
        assert len(list(db.scalars(select(ConsensusSnapshot)))) == 1

    def test_a_later_cutoff_is_a_new_version(self, db: Session) -> None:
        _quotes(db, at=CUTOFF - timedelta(minutes=10), total=47.5)
        self._build(db, at=CUTOFF)
        # A fresh set of quotes inside the staleness window of the later
        # cutoff, so the second build has something eligible to summarise.
        _quotes(db, at=CUTOFF + timedelta(minutes=20), total=49.5)
        later, rep = self._build(db, at=CUTOFF + timedelta(minutes=30))
        assert later is not None
        assert any("CREATED" in r for r in rep.reasons)
        assert len(list(db.scalars(select(ConsensusSnapshot)))) == 2

    def test_different_content_at_the_same_slot_conflicts(self, db: Session) -> None:
        """The quotes are edited under the snapshot, so recomputing the same
        slot produces a different answer. That is a contradiction, not a
        second reading."""
        _quotes(db, at=CUTOFF - timedelta(hours=1), total=47.5)
        first, _ = self._build(db, at=CUTOFF)
        assert first is not None
        original_line = first.median_line

        for q in db.scalars(select(OddsQuote)):
            q.line = 55.5
            q.raw_hash = f"edited{q.id}"
        db.commit()

        returned, rep = self._build(db, at=CUTOFF)
        assert any("CONFLICT" in r for r in rep.reasons), rep.reasons
        # The original stands, unedited.
        assert returned is not None and returned.id == first.id
        assert returned.median_line == original_line
        assert len(list(db.scalars(select(ConsensusSnapshot)))) == 1


class TestAvailabilityIdentity:
    def _assess(self, db: Session, *, at: datetime, player: str = "BUF_QB_ALLEN"):
        from fde_api.forward.injuries import assess_player

        return assess_player(db, canonical_game_id=GAME, team_id="BUF",
                             player_id=player, as_of_at=at, data_mode=MODE)

    def test_first_insertion_then_exact_retry(self, db: Session) -> None:
        first = self._assess(db, at=CUTOFF)
        second = self._assess(db, at=CUTOFF)
        assert first.id == second.id
        assert len(list(db.scalars(select(AvailabilityAssessment)))) == 1
        assert first.logical_identity_hash

    def test_a_later_cutoff_is_a_new_version(self, db: Session) -> None:
        self._assess(db, at=CUTOFF)
        self._assess(db, at=CUTOFF + timedelta(hours=6))
        assert len(list(db.scalars(select(AvailabilityAssessment)))) == 2

    def test_a_different_player_is_a_different_slot(self, db: Session) -> None:
        self._assess(db, at=CUTOFF, player="BUF_QB_ALLEN")
        self._assess(db, at=CUTOFF, player="BUF_WR_DIGGS")
        assert len(list(db.scalars(select(AvailabilityAssessment)))) == 2


class TestPriceObservationIdentity:
    def _record(self, db: Session, *, american: int, at: datetime | None = None,
                user: str = "operator-a"):
        from fde_api.forward.prices import PriceObservation, record_price_observation

        observed = at or CUTOFF
        return record_price_observation(
            db,
            PriceObservation(
                canonical_game_id=GAME, market="SPREAD", selection="HOME",
                line=-3.0, american=american, observed_at=observed,
                user_id=user, cohort=Cohort.FIXTURE,
                provider_mode=ProviderMode.FIXTURE, data_mode=MODE,
            ),
            # `now` must follow the observation: a price seen in the future
            # is refused, which is a separate guard and not what is under
            # test here.
            now=observed + timedelta(minutes=1),
        )

    def test_first_insertion_then_exact_retry(self, db: Session) -> None:
        first = self._record(db, american=-110)
        second = self._record(db, american=-110)
        assert first.id == second.id
        assert len(list(db.scalars(select(ManualBookPriceEntry)))) == 1

    def test_the_same_submission_with_a_different_price_conflicts(
        self, db: Session
    ) -> None:
        """The case the identity exists to catch: one observer reporting two
        prices for one instant has mistyped or is looking at a changed
        market. Storing both as equally valid hides that."""
        from fde_api.forward.prices import PriceEntryError

        first = self._record(db, american=-110)
        with pytest.raises(PriceEntryError, match="different content"):
            self._record(db, american=-105)
        db.rollback()
        rows = list(db.scalars(select(ManualBookPriceEntry)))
        assert len(rows) == 1
        assert rows[0].american == first.american

    def test_a_later_observation_is_a_new_slot(self, db: Session) -> None:
        self._record(db, american=-110)
        self._record(db, american=-105, at=CUTOFF + timedelta(minutes=5))
        assert len(list(db.scalars(select(ManualBookPriceEntry)))) == 2

    def test_a_different_submitter_is_a_new_slot(self, db: Session) -> None:
        self._record(db, american=-110, user="operator-a")
        self._record(db, american=-105, user="operator-b")
        assert len(list(db.scalars(select(ManualBookPriceEntry)))) == 2


class TestTheUpsertPrimitive:
    def test_an_unrelated_integrity_error_is_not_laundered(self, db: Session) -> None:
        """Swallowing every IntegrityError would turn a real constraint bug
        into a silent 'already exists' - the worst way to lose a defect."""
        from sqlalchemy.exc import IntegrityError

        def build_invalid() -> ConsensusSnapshot:
            # data_mode is NOT NULL; this violates a different constraint.
            return ConsensusSnapshot(
                data_mode=None, canonical_game_id=GAME, market="TOTAL",
                method_version="v1", provider_mode="FIXTURE", eligible_books=1,
                quote_ids={}, observed_at=CUTOFF,
            )

        with pytest.raises(IntegrityError):
            upsert_by_identity(
                db, ConsensusSnapshot, identity=CONSENSUS,
                logical_values={"canonical_game_id": GAME, "market": "TOTAL",
                                "cutoff": CUTOFF, "cohort": "DEMO",
                                "method_version": "v1"},
                content_values={"median_line": 1.0},
                build=build_invalid,
            )

    def test_the_result_carries_both_hashes_and_the_outcome(
        self, db: Session
    ) -> None:
        from fde_api.forward.injuries import assess_player

        assess_player(db, canonical_game_id=GAME, team_id="BUF",
                      player_id="BUF_QB_ALLEN", as_of_at=CUTOFF, data_mode=MODE)
        row = db.scalars(select(AvailabilityAssessment)).one()
        assert row.logical_identity_hash and row.content_hash
        assert row.logical_identity_version == LOGICAL_IDENTITY_VERSION

    def test_outcomes_are_the_three_defined_states(self) -> None:
        assert {o.value for o in IdentityOutcome} == {
            "CREATED", "EXISTING_IDENTICAL", "CONFLICT"}

    def test_availability_conflicts_do_not_demand_review(self) -> None:
        """A newer observation supersedes at a later cutoff; the conflict is
        recorded but does not by itself need a human."""
        assert AVAILABILITY.conflict_requires_review is False
        assert CONSENSUS.conflict_requires_review is True
        assert PRICE_OBSERVATION.conflict_requires_review is True
