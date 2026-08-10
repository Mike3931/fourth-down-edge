"""A feature cache must not outlive the data it was computed from.

Feature snapshots are expensive, so they are cached on disk. The key was
the feature-set version, the decay half-life and the horizon — nothing
about the data. That holds until the data changes, and then it silently
stops holding: re-ingest a game with a corrected score, or add a season,
and the file is untouched. Every later run reads features derived from
rows that no longer exist.

Nothing downstream can catch that. The cached values are real feature
values of the right shape in the right range; they are simply answers to
a question about a previous version of the database. It is the same shape
as the other defects found in this codebase — a number that is wrong in a
way that looks exactly like being right.

The cache now carries a fingerprint of the history it was built from.
These tests change the data underneath it and assert the rebuild happens.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from fde_api.backtest.walkforward import build_or_load_features
from fde_api.features.history import GameRow, LeagueHistory, TeamGameRow
from fde_api.pit.clock import PredictionHorizon

KICK = datetime(2024, 9, 8, 17, 0, tzinfo=UTC)


def _history(*, home_score: int = 24, extra_game: bool = False) -> LeagueHistory:
    games = [
        GameRow(
            id=f"2024_0{i}_AAA_BBB", season=2024, week=i, game_type="REG",
            kickoff=KICK + timedelta(days=7 * i),
            home="AAA", away="BBB",
            home_score=home_score if i == 1 else 17, away_score=20,
            result_observed_at=KICK + timedelta(days=7 * i, hours=4),
            roof="dome", surface="turf", home_rest=7, away_rest=7,
            div_game=True, home_qb_id="qb1", away_qb_id="qb2",
            home_coach="c1", away_coach="c2", referee_id="r1",
            stadium_id="S1",
        )
        for i in range(1, 5 + (1 if extra_game else 0))
    ]
    team_rows = [
        TeamGameRow(
            game_id=g.id, season=2024, week=g.week, team=t,
            opponent=("BBB" if t == "AAA" else "AAA"), is_home=(t == "AAA"),
            kickoff=g.kickoff, observed_at=g.result_observed_at,
            stats={"epa_per_play": 0.05, "epa_per_dropback": 0.1, "sack_rate": 0.06},
        )
        for g in games for t in ("AAA", "BBB")
    ]
    return LeagueHistory(games, team_rows)


@pytest.fixture()
def artifacts(monkeypatch, tmp_path):
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    return tmp_path / "artifacts"


def _cache_file(artifacts):
    files = list(artifacts.glob("features_*.json"))
    assert len(files) == 1, files
    return files[0]


class TestTheFingerprintTracksTheData:
    def test_two_identical_histories_share_a_fingerprint(self) -> None:
        assert _history().fingerprint() == _history().fingerprint()

    def test_a_changed_score_changes_it(self) -> None:
        """The case that matters: a corrected result. Same games, same
        ids, same everything else."""
        assert _history(home_score=24).fingerprint() != _history(home_score=31).fingerprint()

    def test_an_added_game_changes_it(self) -> None:
        assert _history().fingerprint() != _history(extra_game=True).fingerprint()


class TestTheCacheIsRebuiltWhenTheDataMoves:
    def test_an_unchanged_history_reuses_the_file(self, artifacts) -> None:
        history = _history()
        build_or_load_features(history, 365.0, PredictionHorizon.PREGAME)
        written = _cache_file(artifacts).stat().st_mtime_ns
        build_or_load_features(history, 365.0, PredictionHorizon.PREGAME)
        assert _cache_file(artifacts).stat().st_mtime_ns == written, "rebuilt unnecessarily"

    def test_a_corrected_score_forces_a_rebuild(self, artifacts) -> None:
        """Under the old key this returned the stale file: same feature
        set, same half-life, same horizon, different data."""
        build_or_load_features(_history(home_score=24), 365.0, PredictionHorizon.PREGAME)
        first = json.loads(_cache_file(artifacts).read_text())

        build_or_load_features(_history(home_score=31), 365.0, PredictionHorizon.PREGAME)
        second = json.loads(_cache_file(artifacts).read_text())

        assert first["history_fingerprint"] != second["history_fingerprint"]

    def test_a_file_from_the_previous_format_is_treated_as_stale(
        self, artifacts
    ) -> None:
        """Old caches are a bare {game_id: values} map with no fingerprint.
        They must not be trusted — that is exactly the state this fixes."""
        history = _history()
        path = artifacts / "features_nfl-core-v1_hl365_PREGAME.json"
        path.write_text(json.dumps({"2024_01_AAA_BBB": {"stale": 1.0}}), encoding="utf-8")

        out = build_or_load_features(history, 365.0, PredictionHorizon.PREGAME)
        assert "stale" not in json.dumps(out)
        assert json.loads(path.read_text())["format"] == 2


class TestTheBuildRecordsWhatItDropped:
    def test_skipped_games_are_named_in_the_file(self, artifacts) -> None:
        """A build that starts dropping games should leave a trace rather
        than a quietly smaller dataset. Early-season games with no prior
        history are legitimately unbuildable."""
        build_or_load_features(_history(), 365.0, PredictionHorizon.PREGAME)
        cached = json.loads(_cache_file(artifacts).read_text())
        assert "games_skipped" in cached
        assert cached["games_covered"] == len(cached["features"])
