"""The yardstick every model is scored against.

`market-benchmark-v1` IS the closing benchmark, and `market-residual-v1`
predicts a correction to it. A wrong value here does not degrade one
model; it moves the reference and every score reported against it, in the
same direction, invisibly.

`load_market_refs` assigned by key, so a second benchmark row for the same
(game, market) silently overwrote the first. In the current database that
is harmless and undetectable at once: there are exactly two rows for every
one of 6,681 (game, market) pairs — the historical ingest is not
idempotent — and every field agrees, so last-write-wins picks an identical
value. Nothing checked, and nothing would have noticed it changing.

Row order out of a bare SELECT is not guaranteed. The day two benchmarks
disagree, the reference silently becomes whichever row the database
handed over last, and the resulting bias is a plausible number that no
report can distinguish from a real one.

Conflicts are now refused rather than resolved. Picking, averaging, or
taking the newest would all be modelling answers to what is a data
question: two conflicting observations of one closing market.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.backtest.walkforward import ConflictingMarketReference, load_market_refs
from fde_api.db.models import Base, Game, OddsSnapshot
from fde_api.util import utc_now

GAME = "2024_01_AAA_BBB"


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.add(Game(id=GAME, season=2024, week=1, game_type="REG",
               home_team_id="AAA", away_team_id="BBB", kickoff_utc=utc_now()))
    s.commit()
    return s


def _benchmark(session: Session, *, market: str, line: float | None = None,
               home: int | None = None, away: int | None = None,
               over: int | None = None, under: int | None = None) -> None:
    """`id` is left to autoincrement, which is exactly how the real
    duplicates arose: 6,681 pairs identical in every column but `id`."""
    session.add(OddsSnapshot(
        game_id=GAME, market=market,
        snapshot_kind="CLOSING_BENCHMARK", book="consensus", line=line,
        home_price_american=home, away_price_american=away,
        over_price_american=over, under_price_american=under,
        observed_at=utc_now(),
    ))
    session.commit()


class TestAgreeingDuplicatesAreAccepted:
    def test_two_identical_rows_load_without_complaint(self, session: Session) -> None:
        """The shape the real database is actually in: 6,681 pairs, all
        agreeing. Refusing these would refuse every backtest."""
        _benchmark(session, market="SPREAD", line=-3.0, home=-110, away=-110)
        _benchmark(session, market="SPREAD", line=-3.0, home=-110, away=-110)
        refs, _ = load_market_refs(session)
        assert refs[GAME].home_line == -3.0

    def test_a_single_row_is_fine(self, session: Session) -> None:
        _benchmark(session, market="SPREAD", line=6.5, home=-105, away=-115)
        refs, _ = load_market_refs(session)
        assert refs[GAME].home_line == 6.5

    def test_different_markets_do_not_collide(self, session: Session) -> None:
        """SPREAD and TOTAL both carry a `line`; they must not be treated
        as duplicates of one another."""
        _benchmark(session, market="SPREAD", line=-3.0, home=-110, away=-110)
        _benchmark(session, market="TOTAL", line=44.5, over=-110, under=-110)
        refs, _ = load_market_refs(session)
        assert refs[GAME].home_line == -3.0
        assert refs[GAME].total_line == 44.5


class TestDisagreeingDuplicatesAreRefused:
    def test_a_conflicting_line_raises_rather_than_picking_one(
        self, session: Session
    ) -> None:
        """The failure that mattered: -3.0 and -6.5 are both plausible
        closing spreads, so whichever won would have looked correct."""
        _benchmark(session, market="SPREAD", line=-3.0, home=-110, away=-110)
        _benchmark(session, market="SPREAD", line=-6.5, home=-110, away=-110)
        with pytest.raises(ConflictingMarketReference) as err:
            load_market_refs(session)
        assert GAME in str(err.value)
        assert "SPREAD" in str(err.value)

    def test_a_conflicting_price_is_refused_too(self, session: Session) -> None:
        """Prices drive the no-vig probabilities the benchmark is scored
        on, so a disagreement there is not cosmetic."""
        _benchmark(session, market="SPREAD", line=-3.0, home=-110, away=-110)
        _benchmark(session, market="SPREAD", line=-3.0, home=-135, away=-110)
        with pytest.raises(ConflictingMarketReference):
            load_market_refs(session)

    def test_a_conflicting_moneyline_is_refused(self, session: Session) -> None:
        _benchmark(session, market="MONEYLINE", home=-150, away=+130)
        _benchmark(session, market="MONEYLINE", home=-150, away=+180)
        with pytest.raises(ConflictingMarketReference):
            load_market_refs(session)

    def test_the_message_names_the_game_and_both_values(
        self, session: Session
    ) -> None:
        """A refusal that does not say what disagreed just moves the
        problem to whoever reads the traceback."""
        _benchmark(session, market="TOTAL", line=44.5, over=-110, under=-110)
        _benchmark(session, market="TOTAL", line=51.0, over=-110, under=-110)
        with pytest.raises(ConflictingMarketReference) as err:
            load_market_refs(session)
        message = str(err.value)
        assert "44.5" in message and "51.0" in message

    def test_it_refuses_rather_than_silently_taking_the_newest(
        self, session: Session
    ) -> None:
        """Order is the whole point: neither row is more authoritative, so
        neither insertion order nor id order may decide it."""
        _benchmark(session, market="SPREAD", line=-3.0, home=-110, away=-110)
        _benchmark(session, market="SPREAD", line=-7.0, home=-110, away=-110)
        with pytest.raises(ConflictingMarketReference):
            load_market_refs(session)
