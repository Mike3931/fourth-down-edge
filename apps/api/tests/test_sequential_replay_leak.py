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
