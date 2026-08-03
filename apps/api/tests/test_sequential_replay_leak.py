"""The sequential replay must never be told the answer first.

`sequential_ratings_moments` evaluates an in-season-updating model by
walking games in kickoff order: advance the clock to a kickoff, observe
every result already visible, then predict.

`ReplayClock.can_see` is inclusive (`observed_at <= now`). So a result
stamped exactly at its own kickoff satisfies it at the moment that game is
being predicted, and the model would be updated with the outcome before
forecasting it. Nothing in the replay prevented that; it was prevented
only by a constant in the loader
(`RESULT_AVAILABILITY_OFFSET = 4h30m`) in a different module.

A leak here does not crash or look wrong. It quietly inflates the measured
skill of the ratings model on the test season, which is the one number the
whole walk-forward exists to produce.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fde_api.backtest.walkforward import sequential_ratings_moments
from fde_api.features.history import GameRow, LeagueHistory
from fde_api.models_ml.ratings import DynamicRatings
from fde_api.pit.guards import LookaheadError

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
OFFSET = timedelta(hours=4, minutes=30)


def _game(gid: str, kickoff: datetime, *, home: str, away: str,
          hs: int | None = 24, as_: int | None = 20,
          observed: datetime | None = None) -> GameRow:
    return GameRow(
        id=gid, season=2026, week=1, game_type="REG", kickoff=kickoff,
        home=home, away=away,
        home_score=hs, away_score=as_,
        result_observed_at=observed if observed is not None else kickoff + OFFSET,
        roof="outdoors", surface="a_turf", home_rest=7, away_rest=7,
        div_game=False, home_qb_id=None, away_qb_id=None,
        home_coach=None, away_coach=None, referee_id=None, stadium_id="BUF00",
    )


def _fitted() -> DynamicRatings:
    """The real driver fits on train+val before replaying the test season,
    so these tests do the same. Fitting on prior-season games only keeps
    the test-season slate untouched."""
    prior = [
        _game(f"p{i}", KICK - timedelta(days=400 - 7 * i), home="BUF", away="KC",
              hs=20 + i, as_=17)
        for i in range(6)
    ]
    m = DynamicRatings(k=0.12)
    m.fit(prior, {}, LeagueHistory(prior, []))
    return m


def _slate() -> list[GameRow]:
    return [
        _game("g1", KICK, home="BUF", away="KC"),
        _game("g2", KICK + timedelta(days=7), home="KC", away="BUF"),
        _game("g3", KICK + timedelta(days=14), home="BUF", away="KC"),
    ]


class TestTheInvariantIsEnforced:
    def test_a_result_stamped_at_its_own_kickoff_is_refused(self) -> None:
        games = _slate()
        games[1] = replace(games[1], result_observed_at=games[1].kickoff)
        with pytest.raises(LookaheadError, match="before its own kickoff"):
            sequential_ratings_moments(_fitted(), games)

    def test_a_result_stamped_before_its_kickoff_is_refused(self) -> None:
        games = _slate()
        games[0] = replace(games[0], result_observed_at=games[0].kickoff - timedelta(hours=1))
        with pytest.raises(LookaheadError, match="before its own kickoff"):
            sequential_ratings_moments(_fitted(), games)

    def test_the_error_names_the_offending_game(self) -> None:
        """An integrity failure that does not say which row is wrong just
        gets rerun until it passes."""
        games = _slate()
        games[2] = replace(games[2], result_observed_at=games[2].kickoff)
        with pytest.raises(LookaheadError, match="g3"):
            sequential_ratings_moments(_fitted(), games)

    def test_the_real_loader_offset_is_accepted(self) -> None:
        """The 4h30m the canonical loader actually stamps must pass."""
        out = sequential_ratings_moments(_fitted(), _slate())
        assert set(out) == {"g1", "g2", "g3"}

    def test_games_without_results_are_allowed(self) -> None:
        """An unplayed game has no result_observed_at and nothing to leak."""
        games = _slate()
        games[2] = replace(games[2], home_score=None, away_score=None, result_observed_at=None)
        out = sequential_ratings_moments(_fitted(), games)
        assert "g3" in out


class TestNoGameInformsItsOwnPrediction:
    def test_predictions_are_produced_for_every_game(self) -> None:
        out = sequential_ratings_moments(_fitted(), _slate())
        assert len(out) == 3

    def test_the_first_game_is_predicted_from_no_observations(self) -> None:
        """Nothing has been observed yet, so the first prediction must equal
        what a freshly-fitted model produces cold."""
        games = _slate()
        out = sequential_ratings_moments(_fitted(), games)
        cold = _fitted().predict(games[0], None)
        assert out["g1"].mu_margin == pytest.approx(cold.mu_margin)

    def test_changing_a_game_result_cannot_change_its_own_prediction(self) -> None:
        """The decisive property. If g1's own outcome reached the model
        before g1 was predicted, flipping that outcome would move the
        prediction. It must not."""
        base = _slate()
        flipped = list(base)
        flipped[0] = replace(base[0], home_score=3, away_score=45)

        out_base = sequential_ratings_moments(_fitted(), base)
        out_flipped = sequential_ratings_moments(_fitted(), flipped)

        assert out_flipped["g1"].mu_margin == pytest.approx(out_base["g1"].mu_margin)

    def test_changing_an_earlier_result_does_change_a_later_prediction(self) -> None:
        """The control. If earlier results did NOT flow forward, the test
        above would pass for the wrong reason - a model that ignores
        everything also never leaks."""
        base = _slate()
        flipped = list(base)
        flipped[0] = replace(base[0], home_score=3, away_score=45)

        out_base = sequential_ratings_moments(_fitted(), base)
        out_flipped = sequential_ratings_moments(_fitted(), flipped)

        assert out_flipped["g3"].mu_margin != pytest.approx(out_base["g3"].mu_margin)


class TestSimultaneousKickoffs:
    """Several games kicking off at the identical instant.

    Iteration order is the risk here, not timestamps. The loop mutates the
    ratings model as it observes results, so if any same-kickoff game's
    result were visible at the shared kickoff, whichever game happened to
    be iterated first would be predicted from a different model state than
    the second. The predictions would depend on list order - a leak that no
    single-game test can surface.
    """

    @staticmethod
    def _simultaneous() -> list[GameRow]:
        # Four teams, two games, one kickoff instant. A later game follows
        # so the control below has something to move.
        return [
            _game("sim_a", KICK, home="BUF", away="KC"),
            _game("sim_b", KICK, home="PHI", away="DAL"),
            _game("later", KICK + timedelta(days=7), home="KC", away="BUF"),
        ]

    def test_all_simultaneous_predictions_use_the_same_pre_kickoff_state(self) -> None:
        games = self._simultaneous()
        out = sequential_ratings_moments(_fitted(), games)
        cold = _fitted()
        assert out["sim_a"].mu_margin == pytest.approx(cold.predict(games[0], None).mu_margin)
        assert out["sim_b"].mu_margin == pytest.approx(cold.predict(games[1], None).mu_margin)

    def test_reversing_the_order_changes_nothing(self) -> None:
        forward = sequential_ratings_moments(_fitted(), self._simultaneous())
        reversed_ = sequential_ratings_moments(_fitted(), list(reversed(self._simultaneous())))
        for gid in ("sim_a", "sim_b", "later"):
            assert reversed_[gid].mu_margin == pytest.approx(forward[gid].mu_margin), gid

    def test_one_simultaneous_result_cannot_influence_the_other(self) -> None:
        """Flip sim_a's outcome. sim_b kicks off at the same instant, so it
        must be unaffected."""
        base = self._simultaneous()
        flipped = list(base)
        flipped[0] = replace(base[0], home_score=3, away_score=45)

        out_base = sequential_ratings_moments(_fitted(), base)
        out_flipped = sequential_ratings_moments(_fitted(), flipped)

        assert out_flipped["sim_b"].mu_margin == pytest.approx(out_base["sim_b"].mu_margin)

    def test_a_later_kickoff_does_respond_to_the_earlier_results(self) -> None:
        """The control. Without this, the two tests above would also pass
        for a replay that never observed anything at all."""
        base = self._simultaneous()
        flipped = list(base)
        flipped[0] = replace(base[0], home_score=3, away_score=45)

        out_base = sequential_ratings_moments(_fitted(), base)
        out_flipped = sequential_ratings_moments(_fitted(), flipped)

        assert out_flipped["later"].mu_margin != pytest.approx(out_base["later"].mu_margin)

    def test_results_become_visible_only_at_their_own_observed_time(self) -> None:
        """A result stamped one microsecond after the shared kickoff is
        still not visible AT that kickoff, so neither simultaneous game
        sees it."""
        games = self._simultaneous()
        games[0] = replace(games[0], result_observed_at=KICK + timedelta(microseconds=1))
        out = sequential_ratings_moments(_fitted(), games)
        cold = _fitted()
        assert out["sim_b"].mu_margin == pytest.approx(cold.predict(games[1], None).mu_margin)


class TestStrictVisibilityIsWhatDefendsUs:
    """`can_see_result` is strict, so the replay does not depend on
    RESULT_AVAILABILITY_OFFSET being positive."""

    def test_a_result_at_exactly_now_is_not_visible(self) -> None:
        from fde_api.pit.clock import ReplayClock

        clock = ReplayClock(KICK)
        assert clock.can_see(KICK) is True           # inclusive, for vintages
        assert clock.can_see_result(KICK) is False   # strict, for results

    def test_a_result_strictly_before_now_is_visible(self) -> None:
        from fde_api.pit.clock import ReplayClock

        clock = ReplayClock(KICK)
        assert clock.can_see_result(KICK - timedelta(microseconds=1)) is True

    def test_a_naive_timestamp_is_never_visible(self) -> None:
        from fde_api.pit.clock import ReplayClock

        clock = ReplayClock(KICK)
        assert clock.can_see_result(datetime(2026, 1, 1)) is False


class TestIngestRefusesMalformedResultTimes:
    """Layer 0: the invariant is enforced when data enters the canonical
    store, not only when the replay consumes it. Relying on a single
    defensive layer is what allowed the original defect."""

    @staticmethod
    def _csv(gametime: str = "13:00") -> bytes:
        header = (
            "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,"
            "home_team,home_score,location,result,total,overtime,old_game_id,gsis,"
            "nfl_detail_id,pfr,pff,espn,ftn,away_rest,home_rest,away_moneyline,"
            "home_moneyline,spread_line,away_spread_odds,home_spread_odds,total_line,"
            "under_odds,over_odds,div_game,roof,surface,temp,wind,away_qb_id,home_qb_id,"
            "away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
        )
        row = dict.fromkeys(header.split(","), "")
        row.update({
            "game_id": "2026_02_KC_BUF", "season": "2026", "game_type": "REG", "week": "2",
            "gameday": "2026-09-13", "gametime": gametime, "away_team": "KC",
            "home_team": "BUF", "away_score": "20", "home_score": "24",
            "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
            "stadium_id": "BUF00", "stadium": "Highmark Stadium",
            "away_rest": "7", "home_rest": "7",
        })
        return ("\n".join([header, ",".join(row[k] for k in header.split(","))]) + "\n").encode()

    def test_a_zero_offset_would_be_refused_at_ingest(self, monkeypatch) -> None:
        """If RESULT_AVAILABILITY_OFFSET were ever set to zero, the loader
        must refuse rather than write a row every downstream consumer has
        to defend against."""
        from datetime import timedelta as _td

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        import fde_api.canonical.load_games as lg
        from fde_api.db.models import Base

        monkeypatch.setattr(lg, "RESULT_AVAILABILITY_OFFSET", _td(0))
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine, future=True)() as s,                 pytest.raises(LookaheadError, match="not strictly after kickoff"):
            lg.load_games_csv(s, self._csv(), seasons=[2026], source_manifest_version="test")

    def test_a_negative_offset_would_be_refused_at_ingest(self, monkeypatch) -> None:
        from datetime import timedelta as _td

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        import fde_api.canonical.load_games as lg
        from fde_api.db.models import Base

        monkeypatch.setattr(lg, "RESULT_AVAILABILITY_OFFSET", _td(hours=-1))
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine, future=True)() as s,                 pytest.raises(LookaheadError, match="not strictly after kickoff"):
            lg.load_games_csv(s, self._csv(), seasons=[2026], source_manifest_version="test")

    def test_the_real_offset_loads_normally(self) -> None:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        import fde_api.canonical.load_games as lg
        from fde_api.db.models import Base, Game

        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine, future=True)() as s:
            lg.load_games_csv(s, self._csv(), seasons=[2026], source_manifest_version="test")
            s.commit()
            g = s.scalars(select(Game)).one()
        assert g.result_observed_at is not None
        assert g.result_observed_at > g.kickoff_utc
