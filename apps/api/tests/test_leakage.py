"""Adversarial point-in-time tests.

Each test deliberately plants future information and asserts the
pipeline REJECTS it. These are release-blocking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fde_api.features import CoreV1Builder, LeagueHistory
from fde_api.pit.clock import PredictionHorizon, ReplayClock, horizon_as_of
from fde_api.pit.guards import LookaheadError, assert_no_lookahead, filter_to_cutoff

NOW = datetime(2025, 11, 1, tzinfo=UTC)


class TestGuards:
    def test_future_record_rejected(self) -> None:
        with pytest.raises(LookaheadError):
            assert_no_lookahead(NOW + timedelta(seconds=1), NOW, "future result")

    def test_past_record_accepted(self) -> None:
        assert_no_lookahead(NOW - timedelta(days=1), NOW)

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(LookaheadError):
            assert_no_lookahead(datetime(2025, 1, 1), NOW)

    def test_filter_excludes_future_and_unknown_provenance(self) -> None:
        rows = [
            {"obs": NOW - timedelta(days=2), "v": "past"},
            {"obs": NOW + timedelta(days=2), "v": "future"},
            {"obs": None, "v": "unknown"},
        ]
        kept = filter_to_cutoff(rows, NOW, lambda r: r["obs"])
        assert [r["v"] for r in kept] == ["past"]


class TestReplayClock:
    def test_clock_never_rewinds(self) -> None:
        clock = ReplayClock(NOW)
        clock.advance_to(NOW + timedelta(days=1))
        with pytest.raises(LookaheadError):
            clock.advance_to(NOW)

    def test_visibility(self) -> None:
        clock = ReplayClock(NOW)
        assert clock.can_see(NOW - timedelta(minutes=1))
        assert not clock.can_see(NOW + timedelta(minutes=1))
        assert not clock.can_see(None)


class TestFeatureLeakage:
    def test_own_game_stats_never_enter_features(self, league_session) -> None:
        h = LeagueHistory.load(league_session)
        game = next(g for g in h.games if g.season == 2024 and g.week == 8)
        as_of = horizon_as_of(game.kickoff, PredictionHorizon.PREGAME)
        rows = h.rows_for(game.home, as_of, exclude_game_id=game.id)
        assert all(r.game_id != game.id for r in rows)
        # Belt and braces: even without the explicit exclusion the row is
        # invisible because its observed_at is after the pregame cutoff.
        rows2 = h.rows_for(game.home, as_of)
        assert all(r.game_id != game.id for r in rows2)

    def test_future_week_results_excluded(self, league_session) -> None:
        h = LeagueHistory.load(league_session)
        game = next(g for g in h.games if g.season == 2024 and g.week == 8)
        as_of = horizon_as_of(game.kickoff, PredictionHorizon.PREGAME)
        rows = h.rows_for(game.home, as_of)
        assert all(r.week < 8 or r.season < 2024 for r in rows)

    def test_snapshot_refuses_observed_result(self, league_session) -> None:
        """Planting a result_observed_at BEFORE the cutoff must abort the build."""
        h = LeagueHistory.load(league_session)
        game = next(g for g in h.games if g.season == 2024 and g.week == 8)
        tampered = type(game)(
            **{**game.__dict__, "result_observed_at": game.kickoff - timedelta(days=3)}
        )
        builder = CoreV1Builder(h)
        with pytest.raises(LookaheadError):
            builder.build(tampered, PredictionHorizon.PREGAME)

    def test_closing_capture_is_not_a_prediction_horizon(self) -> None:
        from fde_api.pit.clock import PREDICTION_HORIZONS

        assert PredictionHorizon.CLOSING_CAPTURE not in PREDICTION_HORIZONS

    def test_earlier_horizon_sees_no_more_than_later(self, league_session) -> None:
        h = LeagueHistory.load(league_session)
        game = next(g for g in h.games if g.season == 2024 and g.week == 8)
        opening = horizon_as_of(game.kickoff, PredictionHorizon.OPENING)
        pregame = horizon_as_of(game.kickoff, PredictionHorizon.PREGAME)
        assert opening < pregame
        n_open = len(h.rows_for(game.home, opening))
        n_pre = len(h.rows_for(game.home, pregame))
        assert n_open <= n_pre


class TestSelectionIsolation:
    def test_walkforward_fold_boundaries(self, league_session) -> None:
        """Train/val/test season sets must be disjoint and ordered."""
        from fde_api.features import LeagueHistory

        h = LeagueHistory.load(league_session)
        n = 2025
        train = {g.season for g in h.games if g.season <= n - 2}
        val = {g.season for g in h.games if g.season == n - 1}
        test = {g.season for g in h.games if g.season == n}
        assert train and val == {n - 1} and test == {n}
        assert max(train) < min(val) <= max(val) < min(test)

    def test_calibration_requires_isotonic_sample(self) -> None:
        from fde_api.calibration import MIN_ISOTONIC_N, fit_calibration

        with pytest.raises(ValueError, match="isotonic requires"):
            fit_calibration("isotonic", [0.5] * (MIN_ISOTONIC_N - 1), [1] * (MIN_ISOTONIC_N - 1))
