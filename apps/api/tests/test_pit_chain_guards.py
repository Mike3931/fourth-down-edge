"""The point-in-time guards, each pinned by a test that fails without it.

Every test here is written to be KILLED by a specific mutation in
`scripts/pit_mutations.py`. That pairing is the point: a temporal guard
with no test behind it can be deleted in a refactor and nothing will
notice until the model is quietly training on the future.

The guards, and the leak each one prevents:

  quote cutoff      a consensus built from quotes that did not exist yet
  weather cutoff    a forecast issued after the decision was made
  injury cutoff     a Sunday report visible to a Tuesday prediction
  result cutoff     a prediction reading the score of its own game
  price cutoff      an evaluation using a price observed after it ran
  tie-breaks        two records at the same instant resolving differently
                    on different runs or different backends

Same-instant ordering deserves its own note. Sorting only by timestamp
leaves ties to whatever order the database returns, which is stable enough
to pass locally and different enough on PostgreSQL to change an answer. The
guard is `(observed_at, id)`, and it is invisible until something depends
on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import (
    InjuryObservation,
    ManualBookPriceEntry,
    OddsQuote,
    ScheduleObservation,
    WeatherForecastVintage,
)
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.modes import DataMode

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
CUTOFF = KICK - timedelta(days=1)
GAME = "2026_02_KC_BUF"
MODE = DataMode.DEMO


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


# --------------------------------------------------------------------------- #
# Quote visibility
# --------------------------------------------------------------------------- #


def _quote(db: Session, *, observed_at: datetime, total: float, book: str) -> None:
    """A two-sided TOTAL quote.

    Totals rather than spreads on purpose: both sides of a total carry the
    SAME number, so the median is unambiguous and the test measures cutoff
    visibility rather than a sign convention.
    """
    for selection in ("OVER", "UNDER"):
        db.add(OddsQuote(
            data_mode=MODE.value, canonical_game_id=GAME, provider="fixture",
            provider_mode=ProviderMode.FIXTURE.value, sportsbook=book,
            market="TOTAL", selection=selection, line=total, american=-110,
            decimal_odds=1.909, observed_at=observed_at,
            provider_timestamp=observed_at,
            raw_hash=f"{book}{observed_at.isoformat()}{selection}",
        ))
    db.commit()


class TestAQuoteAfterTheCutoffIsInvisible:
    """Killed by: quote_visibility_after_cutoff."""

    def test_a_later_quote_does_not_enter_the_snapshot(self, db: Session) -> None:
        from fde_api.forward.consensus import build_consensus

        for book in ("draftkings", "fanduel", "betmgm"):
            _quote(db, observed_at=CUTOFF - timedelta(hours=1), total=47.5, book=book)
        # A sharply different total arrives AFTER the cutoff. If it were
        # visible the median would move, and the snapshot would encode
        # information the decision could not have had.
        # The SAME books, quoting again after the cutoff. Naming them
        # "<book>-late" was my first attempt and it proved nothing: an
        # unrecognised sportsbook is rejected by a different guard entirely,
        # so the mutation sweep found this test surviving the removal of the
        # cutoff it was supposed to be testing.
        for book in ("draftkings", "fanduel", "betmgm"):
            _quote(db, observed_at=CUTOFF + timedelta(hours=2), total=61.5,
                   book=book)

        snap, report = build_consensus(
            db, canonical_game_id=GAME, market="TOTAL", as_of_at=CUTOFF,
            kickoff_utc=KICK, data_mode=MODE,
        )
        assert snap is not None, report.reasons
        assert snap.median_line == pytest.approx(47.5), (
            f"a post-cutoff quote reached the snapshot: median {snap.median_line}"
        )


# --------------------------------------------------------------------------- #
# Weather visibility
# --------------------------------------------------------------------------- #


class TestAForecastAfterTheCutoffIsInvisible:
    """Killed by: weather_visibility_after_cutoff."""

    def test_a_later_vintage_is_not_returned(self, db: Session) -> None:
        from fde_api.forward.weather import forecast_as_of

        for observed, temp, tag in (
            (CUTOFF - timedelta(hours=3), 60, "early"),
            (CUTOFF + timedelta(hours=3), 20, "late"),
        ):
            db.add(WeatherForecastVintage(
                data_mode=MODE.value, canonical_game_id=GAME, provider="nws",
                observed_at=observed, temp_f=temp, raw_hash=f"wx-{tag}",
            ))
        db.commit()

        vintage = forecast_as_of(
            db, canonical_game_id=GAME, as_of_at=CUTOFF, data_mode=MODE)
        assert vintage is not None
        assert vintage.temp_f == 60, "a forecast issued after the cutoff was returned"


# --------------------------------------------------------------------------- #
# Injury visibility
# --------------------------------------------------------------------------- #


class TestAnInjuryReportAfterTheCutoffIsInvisible:
    """Killed by: injury_visibility_after_cutoff."""

    def test_a_later_report_is_not_returned(self, db: Session) -> None:
        from fde_api.forward.injuries import observations_as_of

        for observed, designation, ref in (
            (CUTOFF - timedelta(hours=6), "QUESTIONABLE", "tuesday"),
            (CUTOFF + timedelta(hours=6), "OUT", "sunday"),
        ):
            db.add(InjuryObservation(
                data_mode=MODE.value, canonical_game_id=GAME, team_id="BUF",
                player_id="BUF_QB_ALLEN", report_date="2026-09-11",
                game_designation=designation, practice_status="LIMITED",
                source_category="OFFICIAL_VERIFIED", source_reference=ref,
                confidence="HIGH", verification_status="VERIFIED",
                observed_at=observed, entered_at=observed,
            ))
        db.commit()

        rows = observations_as_of(
            db, canonical_game_id=GAME, as_of_at=CUTOFF, data_mode=MODE)
        designations = {r.game_designation for r in rows.values()} if isinstance(
            rows, dict) else {r.game_designation for r in rows}
        assert "OUT" not in designations, (
            "a Sunday injury report was visible to a Tuesday cutoff"
        )


# --------------------------------------------------------------------------- #
# Result visibility
# --------------------------------------------------------------------------- #


def _schedule(db: Session, *, observed_at: datetime, status: str = "SCHEDULED",
              summary: str | None = None) -> ScheduleObservation:
    row = ScheduleObservation(
        data_mode=MODE.value, canonical_game_id=GAME, provider="fixture",
        provider_game_id="evt1", season=2026, season_type="REG", week=2,
        home_team_id="BUF", away_team_id="KC", kickoff_utc=KICK,
        neutral_site=False, international=False, game_status=status,
        content_hash=f"{status}-{observed_at.isoformat()}", observed_at=observed_at,
        change_summary=summary,
    )
    db.add(row)
    db.commit()
    return row


class TestAResultCannotReachItsOwnPrediction:
    """Killed by: result_visible_to_its_own_prediction, result_before_kickoff_accepted."""

    def test_the_result_is_invisible_before_it_is_observed(self, db: Session) -> None:
        from fde_api.forward.results import ResultObservation, ingest_result, result_scores

        _schedule(db, observed_at=KICK - timedelta(days=30))
        ingest_result(
            db,
            ResultObservation(canonical_game_id=GAME, home_score=24, away_score=20,
                              observed_at=KICK + timedelta(hours=4)),
            data_mode=MODE,
        )
        db.commit()

        # At the prediction cutoff the game has not been played.
        assert result_scores(db, canonical_game_id=GAME, data_mode=MODE,
                             as_of=CUTOFF) is None
        # Afterwards it has.
        assert result_scores(db, canonical_game_id=GAME, data_mode=MODE,
                             as_of=KICK + timedelta(hours=5)) == (24, 20)

    def test_a_result_at_the_same_instant_as_the_cutoff_is_visible(
        self, db: Session
    ) -> None:
        """The certified rule for results is STRICT for predictions but the
        lookup itself is inclusive at its own as-of: asking "what was known
        AT time t" includes what was observed at t. The strictness lives in
        the prediction cutoff, not here, and pinning it stops the two rules
        drifting into each other."""
        from fde_api.forward.results import ResultObservation, ingest_result, result_scores

        _schedule(db, observed_at=KICK - timedelta(days=30))
        seen = KICK + timedelta(hours=4)
        ingest_result(
            db,
            ResultObservation(canonical_game_id=GAME, home_score=24, away_score=20,
                              observed_at=seen),
            data_mode=MODE,
        )
        db.commit()
        assert result_scores(db, canonical_game_id=GAME, data_mode=MODE,
                             as_of=seen) == (24, 20)
        assert result_scores(db, canonical_game_id=GAME, data_mode=MODE,
                             as_of=seen - timedelta(microseconds=1)) is None

    def test_a_result_observed_before_kickoff_is_refused(self, db: Session) -> None:
        from fde_api.forward.results import (
            ResultIngestionError,
            ResultObservation,
            ingest_result,
        )

        _schedule(db, observed_at=KICK - timedelta(days=30))
        with pytest.raises(ResultIngestionError, match="before it starts"):
            ingest_result(
                db,
                ResultObservation(canonical_game_id=GAME, home_score=24, away_score=20,
                                  observed_at=KICK - timedelta(hours=1)),
                data_mode=MODE,
            )


class TestSimultaneousResultsOrderDeterministically:
    """Killed by: simultaneous_result_ordering_becomes_arbitrary."""

    def test_two_results_at_one_instant_resolve_by_id(self, db: Session) -> None:
        from fde_api.forward.results import final_observation

        _schedule(db, observed_at=KICK - timedelta(days=30))
        instant = KICK + timedelta(hours=4)
        for score in ("final 24-20 from a", "final 31-17 from b"):
            _schedule(db, observed_at=instant, status="FINAL", summary=score)

        first = final_observation(db, canonical_game_id=GAME, data_mode=MODE)
        # Repeat the read: a tie broken by id gives the same answer every
        # time, one broken by nothing gives whatever the database returns.
        for _ in range(5):
            again = final_observation(db, canonical_game_id=GAME, data_mode=MODE)
            assert again is not None and first is not None
            assert again.id == first.id
        assert first is not None
        assert first.id == max(
            o.id for o in db.query(ScheduleObservation).filter_by(game_status="FINAL")
        ), "the tie did not resolve to the highest id"


# --------------------------------------------------------------------------- #
# Price visibility
# --------------------------------------------------------------------------- #


def _price(
    db: Session, *, observed_at: datetime, american: int,
    user_id: str = "fixture-operator",
) -> ManualBookPriceEntry:
    from fde_api.forward.prices import PriceObservation, record_price_observation

    return record_price_observation(
        db,
        PriceObservation(
            canonical_game_id=GAME, market="SPREAD", selection="HOME", line=-3.0,
            american=american, observed_at=observed_at, user_id=user_id,
            cohort=Cohort.FIXTURE, provider_mode=ProviderMode.FIXTURE,
            data_mode=MODE,
        ),
        now=observed_at,
    )


class TestALaterPriceCannotEnterAnEarlierEvaluation:
    """Killed by: later_price_enters_earlier_evaluation."""

    def test_the_as_of_lookup_ignores_a_later_price(self, db: Session) -> None:
        from fde_api.forward.prices import current_price

        _price(db, observed_at=CUTOFF - timedelta(hours=1), american=-110)
        _price(db, observed_at=CUTOFF + timedelta(hours=1), american=+250)
        db.commit()

        found = current_price(
            db, canonical_game_id=GAME, market="SPREAD", selection="HOME",
            cohort=Cohort.FIXTURE, as_of=CUTOFF,
        )
        assert found is not None
        assert found.american == -110, (
            "a price observed after the cutoff was used for an earlier evaluation"
        )


class TestSimultaneousPricesOrderDeterministically:
    """Killed by: simultaneous_ordering_becomes_arbitrary.

    The docstring used to name `simultaneous_price_ordering_becomes_arbitrary`,
    which is not one of the nine mutations `pit_mutations.py` defines. A
    "Killed by:" line is a provenance claim, and that one could never have
    been checked.

    Worse, the test below killed the real mutation ONLY SOMETIMES.
    `ManualBookPriceEntry.id` is a random hex string, so `max(a.id, b.id)`
    is a LEXICOGRAPHIC comparison unrelated to insertion order. Under the
    mutation the query returns rows in insertion order and `max` takes the
    first, so the test passed whenever the first-inserted row happened to
    own the lexicographically higher id — measured at 3 survivals in 10
    runs. The committed mutation artifact recorded this test as a killer
    because that run landed on the other side of the coin.

    A mutation detector that flips a coin is worse than one that is absent,
    because the artifact reports it as evidence. The ids are therefore
    pinned rather than drawn: the row written FIRST is given the LOWER id,
    which is the adversarial arrangement. Under the mutation the query
    returns insertion order and `max` takes the first, so it answers with
    the lower id every run; the tie-break answers with the higher. The two
    can no longer coincide by luck.
    """

    def test_two_prices_at_one_instant_resolve_by_id(
        self, db: Session, monkeypatch
    ) -> None:
        """Two DIFFERENT submissions at the same instant.

        Different submitters, so these are two legitimate slots rather than
        one contradicted slot - the same observer reporting two prices for
        one instant is now a CONFLICT the database refuses, which is a
        separate property tested elsewhere. What is exercised here is the
        tie-break: two valid rows sharing a timestamp must resolve the same
        way on every read and every backend.
        """
        import uuid as _uuid

        from fde_api.forward.prices import current_price

        # `id` is `px_{uuid4().hex[:20]}`, so only the top 80 bits reach the
        # id. Shifting puts the distinguishing digits where they land.
        ids = iter([
            _uuid.UUID(int=0x11111111111111111111 << 48),
            _uuid.UUID(int=0x22222222222222222222 << 48),
        ])
        monkeypatch.setattr("fde_api.forward.prices.uuid.uuid4", lambda: next(ids))

        instant = CUTOFF - timedelta(hours=2)
        low = _price(db, observed_at=instant, american=-110, user_id="operator-a")
        high = _price(db, observed_at=instant, american=-105, user_id="operator-b")
        db.commit()

        assert low.id < high.id, "the fixture must write the lower id first"

        picked = {
            current_price(
                db, canonical_game_id=GAME, market="SPREAD", selection="HOME",
                cohort=Cohort.FIXTURE, as_of=CUTOFF,
            ).id
            for _ in range(5)
        }
        assert len(picked) == 1, f"a simultaneous pair resolved inconsistently: {picked}"
        assert picked == {high.id}, (
            "the tie resolved to insertion order, not to the highest id"
        )


class TestTheSharedTieBreakRule:
    """Killed by: simultaneous_ordering_becomes_arbitrary, ordered_listing_becomes_arbitrary.

    Tested directly against the helper rather than through a database.
    Going through SQLite is what made this guard invisible: rows come back
    in insertion order, insertion order matches id order, and Python's sort
    is stable, so dropping the id tie-break changed nothing observable. A
    deliberately shuffled list has no such accident to hide behind.
    """

    def _rows(self) -> list[tuple[int, datetime]]:
        instant = KICK
        # Ids deliberately out of order relative to position, so a stable
        # sort that ignores the id gives a different answer from one that
        # does not.
        return [(7, instant), (3, instant), (11, instant), (5, instant)]

    def test_a_tie_resolves_to_the_highest_identity(self) -> None:
        from fde_api.forward.ordering import newest

        picked = newest(self._rows(), when=lambda r: r[1], ident=lambda r: r[0])
        assert picked is not None
        assert picked[0] == 11, f"tie resolved to {picked[0]}, not the highest id"

    def test_the_answer_does_not_depend_on_input_order(self) -> None:
        from fde_api.forward.ordering import newest

        rows = self._rows()
        forward = newest(rows, when=lambda r: r[1], ident=lambda r: r[0])
        backward = newest(list(reversed(rows)), when=lambda r: r[1],
                          ident=lambda r: r[0])
        assert forward == backward, (
            "reversing the input changed the answer; the tie-break is not doing its job"
        )

    def test_a_later_timestamp_still_wins_over_a_higher_id(self) -> None:
        """Time first, identity only as the tie-break."""
        from fde_api.forward.ordering import newest

        rows = [(99, KICK - timedelta(hours=1)), (1, KICK)]
        picked = newest(rows, when=lambda r: r[1], ident=lambda r: r[0])
        assert picked is not None and picked[0] == 1

    def test_an_empty_sequence_has_no_newest(self) -> None:
        from fde_api.forward.ordering import newest

        assert newest([], when=lambda r: r[1], ident=lambda r: r[0]) is None

    def test_a_listing_is_reproducible_regardless_of_input_order(self) -> None:
        from fde_api.forward.ordering import order_by_time_then_id

        rows = self._rows()
        a = order_by_time_then_id(rows, when=lambda r: r[1], ident=lambda r: r[0])
        b = order_by_time_then_id(list(reversed(rows)), when=lambda r: r[1],
                                  ident=lambda r: r[0])
        assert a == b
        assert [r[0] for r in a] == [3, 5, 7, 11]
