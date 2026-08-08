"""Capturing the same market twice must not invent price movement.

Three defects, all found by running the capture against a real game after
it had finished:

  * The quote hash included the CAPTURE INSTANT, so two runs over an
    unchanged market produced different hashes, the duplicate check never
    matched, and every re-run appended a full set of rows. Unbounded
    growth, and a price history showing movement that never happened.

  * `game_status` was hardcoded to SCHEDULED, so a finished game was
    recorded as upcoming forever.

  * The final score sat in the provider payload and was thrown away.

The fix to the second broke the third, which is the interesting part.
Stamping FINAL on the schedule observation created a record claiming the
game had ended while carrying no score, so the real score arriving
afterwards was refused as a correction to a final that already existed.
The guard was right and the status was the lie: FINAL means "a result has
been recorded" and belongs to `ingest_result` alone.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import OddsQuote, ScheduleObservation
from fde_api.db.models import Base

CAPTURE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "espn_live_capture.py"


def _module():
    spec = importlib.util.spec_from_file_location("espn_live_capture", CAPTURE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def capture():
    return _module()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
    from fde_api.config import settings

    monkeypatch.setattr(settings, "database_url", url)
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    import fde_api.db.engine as db_engine

    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    yield sessionmaker(bind=engine, future=True)
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _game(*, status: str, completed: bool, quotes: list[dict]) -> dict:
    return {
        "provider_event_id": "1", "canonical_game_id": "2026_PRE_0807_CAR_ARI",
        "name": "Carolina at Arizona", "season": 2026, "season_type": "PRE",
        "home": "ARI", "away": "CAR",
        "kickoff_utc": datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
        "venue": "Tom Benson Hall of Fame Stadium", "neutral_site": True,
        "status": status, "completed": completed,
        "scores": {"home": 30, "away": 33},
        "books": sorted({q["sportsbook"] for q in quotes}), "quotes": quotes,
    }


PRICES = [
    {"sportsbook": "draftkings", "market": "SPREAD", "selection": "HOME",
     "line": 1.5, "american": -115},
    {"sportsbook": "draftkings", "market": "TOTAL", "selection": "OVER",
     "line": 34.5, "american": -110},
]


class TestRecapturingAnUnchangedMarketAddsNothing:
    def test_a_second_identical_capture_writes_no_quotes(self, capture, db) -> None:
        game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
        first = capture.persist([game], data_mode_value="LIVE_RESEARCH")
        second = capture.persist([game], data_mode_value="LIVE_RESEARCH")
        assert first["quotes_written"] == len(PRICES)
        assert second["quotes_written"] == 0, "the capture instant is back in the hash"
        assert second["quotes_duplicate"] == len(PRICES)

    def test_the_quote_table_does_not_grow(self, capture, db) -> None:
        game = _game(status="STATUS_SCHEDULED", completed=False, quotes=PRICES)
        for _ in range(4):
            capture.persist([game], data_mode_value="LIVE_RESEARCH")
        with db() as s:
            assert s.scalar(select(func.count()).select_from(OddsQuote)) == len(PRICES)

    def test_a_genuinely_changed_price_is_a_new_row(self, capture, db) -> None:
        """Dedup must not swallow real movement."""
        capture.persist([_game(status="STATUS_SCHEDULED", completed=False,
                               quotes=PRICES)], data_mode_value="LIVE_RESEARCH")
        moved = [{**PRICES[0], "american": -120}, PRICES[1]]
        result = capture.persist([_game(status="STATUS_SCHEDULED", completed=False,
                                        quotes=moved)], data_mode_value="LIVE_RESEARCH")
        assert result["quotes_written"] == 1
        with db() as s:
            prices = sorted(
                q.american for q in s.scalars(
                    select(OddsQuote).where(OddsQuote.market == "SPREAD"))
            )
        assert prices == [-120, -115]


class TestTheStatusComesFromTheProvider:
    @pytest.mark.parametrize(
        ("espn", "expected"),
        [("STATUS_SCHEDULED", "SCHEDULED"), ("STATUS_POSTPONED", "POSTPONED"),
         ("STATUS_CANCELED", "CANCELLED"), ("SOMETHING_NEW", "UNRESOLVED")],
    )
    def test_it_is_mapped_not_hardcoded(self, capture, db, espn, expected) -> None:
        capture.persist([_game(status=espn, completed=False, quotes=[])],
                        data_mode_value="LIVE_RESEARCH")
        with db() as s:
            obs = s.scalars(select(ScheduleObservation)).first()
        assert obs is not None
        assert obs.game_status == expected

    def test_a_finished_game_is_not_stamped_final_by_the_schedule(
        self, capture, db
    ) -> None:
        """FINAL belongs to the results path, which carries the score.

        A schedule observation claiming FINAL with no score made the real
        score look like a correction to an existing final, and it was
        refused.
        """
        capture.persist([_game(status="STATUS_FINAL", completed=True, quotes=[])],
                        data_mode_value="LIVE_RESEARCH")
        with db() as s:
            statuses = [o.game_status for o in s.scalars(
                select(ScheduleObservation).order_by(ScheduleObservation.id))]
        assert statuses[0] == "SCHEDULED", "the fixture row must not claim FINAL"


class TestTheFinalScoreIsRecorded:
    def test_a_completed_game_gets_a_final_observation(self, capture, db) -> None:
        counts = capture.persist(
            [_game(status="STATUS_FINAL", completed=True, quotes=[])],
            data_mode_value="LIVE_RESEARCH")
        assert counts["results_recorded"] == 1
        with db() as s:
            rows = list(s.scalars(
                select(ScheduleObservation).order_by(ScheduleObservation.id)))
        assert [r.game_status for r in rows] == ["SCHEDULED", "FINAL"]
        assert "30-33" in (rows[1].change_summary or "")

    def test_recording_it_twice_is_a_no_op(self, capture, db) -> None:
        game = _game(status="STATUS_FINAL", completed=True, quotes=[])
        capture.persist([game], data_mode_value="LIVE_RESEARCH")
        second = capture.persist([game], data_mode_value="LIVE_RESEARCH")
        assert second["results_recorded"] == 0
        with db() as s:
            finals = [o for o in s.scalars(select(ScheduleObservation))
                      if o.game_status == "FINAL"]
        assert len(finals) == 1

    def test_an_incomplete_game_records_no_result(self, capture, db) -> None:
        counts = capture.persist(
            [_game(status="STATUS_SCHEDULED", completed=False, quotes=[])],
            data_mode_value="LIVE_RESEARCH")
        assert counts["results_recorded"] == 0
        with db() as s:
            assert not [o for o in s.scalars(select(ScheduleObservation))
                        if o.game_status == "FINAL"]


class TestTheSpreadSignIsNeverInferredFromPosition:
    """The per-side block is the source of truth for a handicap.

    When it was missing the code fell back to the scalar `spread`, which is
    unsigned with respect to a side, and inferred the sign from position -
    getting it exactly backwards. It recorded the favourite as the
    underdog, and nothing about that is visible afterwards: an inverted
    line is a perfectly plausible number.

    The same shape as the consensus median bug, one layer earlier. That one
    was caught because a spread of zero looked odd; this one would not have
    looked odd at all.
    """

    HOME_DOG: ClassVar[dict] = {
        "provider": {"name": "DraftKings"}, "spread": 1.5,
        "moneyline": {}, "total": {},
        "homeTeamOdds": {"favorite": False, "underdog": True},
        "awayTeamOdds": {"favorite": True, "underdog": False},
    }

    def _spreads(self, odds: dict) -> dict[str, float]:
        module = _module()
        return {
            q["selection"]: q["line"]
            for q in module._quotes_from_odds(odds, home="ARI", away="CAR")
            if q["market"] == "SPREAD"
        }

    def test_the_per_side_line_is_used_when_present(self) -> None:
        odds = {**self.HOME_DOG, "pointSpread": {
            "home": {"close": {"line": "+1.5", "odds": "-115"}},
            "away": {"close": {"line": "-1.5", "odds": "-105"}}}}
        assert self._spreads(odds) == {"HOME": 1.5, "AWAY": -1.5}

    def test_a_missing_line_takes_its_sign_from_the_favourite_flag(self) -> None:
        """The bug returned HOME -1.5 / AWAY +1.5 here - inverted."""
        odds = {**self.HOME_DOG, "pointSpread": {
            "home": {"close": {"odds": "-115"}},
            "away": {"close": {"odds": "-105"}}}}
        assert self._spreads(odds) == {"HOME": 1.5, "AWAY": -1.5}

    def test_a_home_favourite_gets_the_negative_number(self) -> None:
        odds = {
            "provider": {"name": "DraftKings"}, "spread": 3.0,
            "moneyline": {}, "total": {},
            "homeTeamOdds": {"favorite": True, "underdog": False},
            "awayTeamOdds": {"favorite": False, "underdog": True},
            "pointSpread": {"home": {"close": {"odds": "-110"}},
                            "away": {"close": {"odds": "-110"}}},
        }
        assert self._spreads(odds) == {"HOME": -3.0, "AWAY": 3.0}

    def test_one_side_stating_it_is_enough(self) -> None:
        """Only the away block is populated; the home sign follows."""
        odds = {
            "provider": {"name": "DraftKings"}, "spread": 2.5,
            "moneyline": {}, "total": {},
            "awayTeamOdds": {"favorite": True},
            "pointSpread": {"home": {"close": {"odds": "-110"}},
                            "away": {"close": {"odds": "-110"}}},
        }
        assert self._spreads(odds) == {"HOME": 2.5, "AWAY": -2.5}

    def test_nothing_is_emitted_when_the_sign_is_unknowable(self) -> None:
        """No line and no flags. A guess here is a coin flip recorded as a
        fact, so the quote is dropped instead - the same rule already
        applied to a missing price."""
        odds = {
            "provider": {"name": "DraftKings"}, "spread": 1.5,
            "moneyline": {}, "total": {},
            "pointSpread": {"home": {"close": {"odds": "-115"}},
                            "away": {"close": {"odds": "-105"}}},
        }
        assert self._spreads(odds) == {}

    def test_a_missing_spread_scalar_emits_nothing(self) -> None:
        odds = {**self.HOME_DOG, "spread": None, "pointSpread": {
            "home": {"close": {"odds": "-115"}},
            "away": {"close": {"odds": "-105"}}}}
        assert self._spreads(odds) == {}
