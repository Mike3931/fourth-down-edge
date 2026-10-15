"""Controlled fixtures exercise mechanics, never counted as live forecast evidence."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from fde_api.config import settings
from fde_api.db.forward_models import GameIdentityReview, ScheduleObservation
from fde_api.db.models import FeatureSnapshot, Game, ModelEvaluation, ModelVersion, Prediction, Team
from fde_api.db.research_models import ResearchSource
from fde_api.forward.prospective import (
    CHECKPOINTS,
    checkpoint,
    evaluate_forecasts,
    generate_forecasts,
    read_prospective,
)

NOW = datetime(2020, 10, 14, 12, tzinfo=UTC)
GAME = "2020_06_SEA_DEN"


def schedule(s, gid=GAME, *, status="SCHEDULED", at=NOW, kickoff=None, summary=None, provider="nflverse"):
    row = ScheduleObservation(
        data_mode="LIVE_RESEARCH",
        canonical_game_id=gid,
        provider=provider,
        provider_game_id=gid,
        season=2020,
        season_type="REG",
        week=6,
        home_team_id="DEN",
        away_team_id="SEA",
        kickoff_utc=kickoff or NOW + timedelta(hours=48),
        game_status=status,
        content_hash=str(at) + status,
        observed_at=at,
        change_summary=summary,
    )
    s.add(row)
    s.flush()
    return row


@pytest.fixture()
def cohort(session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(settings, "raw_dir", tmp_path / "raw")
    session.add_all([Team(id="SEA", name="Seattle"), Team(id="DEN", name="Denver")])
    session.flush()
    session.add(
        Game(
            id=GAME,
            season=2020,
            week=6,
            game_type="REG",
            kickoff_utc=NOW + timedelta(hours=48),
            home_team_id="DEN",
            away_team_id="SEA",
        )
    )
    for i in range(32):
        past = schedule(
            session,
            f"past-{i}",
            status="FINAL",
            at=NOW - timedelta(days=1),
            kickoff=NOW - timedelta(days=40 - i),
            summary=f"final {20 + i % 15}-{17 + i % 9} from nflverse",
        )
        past.week = 5
    schedule(session)
    session.commit()
    return session


def test_actual_time_lineage_retry_and_new_evidence_are_immutable(cohort):
    s = cohort
    assert generate_forecasts(s, now=NOW)["created"] == 3
    s.commit()
    before = {p.id: (p.outputs.copy(), p.as_of_at, p.model_version_id) for p in s.scalars(select(Prediction))}
    assert generate_forecasts(s, now=NOW + timedelta(minutes=1))["created"] == 0
    assert (
        generate_forecasts(
            s,
            now=NOW + timedelta(minutes=1),
            checkpoints={"PROS_EARLY": 168, "PROS_72H": 72, "PROS_24H": 24, "PROS_FINAL": 1.5},
        )["created"]
        == 0
    )
    s.add(
        ResearchSource(
            id="new",
            dataset="injuries",
            season=2020,
            provider="nflverse",
            payload_hash="hash",
            source_url="https://example.test",
            published_at=None,
            captured_at=NOW + timedelta(minutes=2),
            coverage={},
        )
    )
    s.flush()
    assert generate_forecasts(s, now=NOW + timedelta(minutes=3))["created"] == 3
    for pid, values in before.items():
        p = s.get(Prediction, pid)
        assert (p.outputs, p.as_of_at, p.model_version_id) == values
        f = s.get(FeatureSnapshot, p.feature_snapshot_id)
        assert f.as_of_at == f.created_at == p.created_at == NOW
        assert len(f.values["training_result_observation_ids"]) == 32
        assert s.get(ModelVersion, p.model_version_id).approval_status == "research_only"
    assert read_prospective(s, now=NOW + timedelta(minutes=4))["count"] == 6
    assert len(read_prospective(s, now=NOW)["records"]) == 3


def test_future_results_and_source_revisions_cannot_enter_prior_forecast(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    first = list(s.scalars(select(Prediction)))
    oldoutputs = [p.outputs.copy() for p in first]
    schedule(
        s,
        "future-known",
        status="FINAL",
        at=NOW + timedelta(days=3),
        kickoff=NOW - timedelta(days=2),
        summary="final 99-0 from nflverse",
    )
    s.add(
        ResearchSource(
            id="future-source",
            dataset="injuries",
            season=2020,
            provider="nflverse",
            payload_hash="x",
            source_url="https://example.test",
            published_at=NOW + timedelta(days=1),
            captured_at=NOW - timedelta(seconds=1),
            coverage={},
        )
    )
    s.flush()
    assert generate_forecasts(s, now=NOW + timedelta(minutes=1))["created"] == 0
    assert [p.outputs for p in first] == oldoutputs
    assert all("future-source" not in str(f.values) for f in s.scalars(select(FeatureSnapshot)))


@pytest.mark.parametrize("status", ["POSTPONED", "CANCELLED", "IN_PROGRESS", "FINAL"])
def test_no_post_kickoff_or_non_scheduled_forecasts(cohort, status):
    schedule(cohort, status=status, at=NOW + timedelta(seconds=1))
    assert generate_forecasts(cohort, now=NOW + timedelta(seconds=2))["created"] == 0
    assert generate_forecasts(cohort, now=NOW + timedelta(days=3))["created"] == 0


def test_stale_ambiguous_and_incomplete_final_are_withheld(cohort):
    s = cohort
    assert generate_forecasts(s, now=NOW + timedelta(hours=25))["created"] == 0
    schedule(s, at=NOW + timedelta(hours=47))
    assert generate_forecasts(s, now=NOW + timedelta(hours=47))["created"] == 0
    s.add(
        GameIdentityReview(
            id="ambiguous",
            data_mode="LIVE_RESEARCH",
            provider="nflverse",
            provider_game_id=GAME,
            reason="AMBIGUOUS",
            evidence={"canonical_game_id": GAME},
            created_at=NOW,
        )
    )
    s.flush()
    assert generate_forecasts(s, now=NOW)["created"] == 0


def test_settlement_waits_for_observed_final_and_preserves_corrections(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    assert evaluate_forecasts(s, now=NOW + timedelta(days=1))["evaluations_added"] == 0
    finaltime = NOW + timedelta(days=3)
    schedule(s, status="FINAL", at=finaltime, summary="final 24-17 from nflverse")
    assert evaluate_forecasts(s, now=finaltime - timedelta(seconds=1))["evaluations_added"] == 0
    assert evaluate_forecasts(s, now=finaltime)["evaluations_added"] == 3
    assert evaluate_forecasts(s, now=finaltime)["evaluations_added"] == 0
    original = {e.id: e.metrics.copy() for e in s.scalars(select(ModelEvaluation))}
    report = read_prospective(s, now=finaltime)
    assert report["evaluated_forecasts"] == 3
    assert all(r["n"] == 1 for r in report["performance"])
    schedule(
        s,
        status="FINAL",
        at=finaltime + timedelta(hours=1),
        summary="final 23-17 from nflverse (correction: official correction)",
    )
    assert evaluate_forecasts(s, now=finaltime + timedelta(hours=1))["evaluations_added"] == 3
    assert all(s.get(ModelEvaluation, k).metrics == v for k, v in original.items())
    assert len(read_prospective(s, now=finaltime + timedelta(hours=1))["records"]) == 3
    assert all(
        r["evaluation"]["home_score"] == 23
        for r in read_prospective(s, now=finaltime + timedelta(hours=1))["records"]
    )


def test_provider_conflict_withholds_new_and_previous_evaluations(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    t = NOW + timedelta(days=3)
    schedule(s, status="FINAL", at=t, summary="final 24-17 from nflverse")
    evaluate_forecasts(s, now=t)
    schedule(s, status="FINAL", at=t + timedelta(seconds=1), summary="final 23-17 from espn", provider="espn")
    assert evaluate_forecasts(s, now=t + timedelta(seconds=1))["evaluations_added"] == 0
    assert read_prospective(s, now=t + timedelta(seconds=1))["evaluated_forecasts"] == 0
    assert len(list(s.scalars(select(ModelEvaluation)))) == 3


def test_ties_excluded_from_moneyline_but_scored_margin_total(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    t = NOW + timedelta(days=3)
    schedule(s, status="FINAL", at=t, summary="final 20-20 from nflverse")
    evaluate_forecasts(s, now=t)
    for row in read_prospective(s, now=t)["performance"]:
        assert row["ties"] == 1 and row["n_moneyline"] == 0 and row["brier"] is None
        assert row["margin_mae"] is not None


def test_reschedule_keeps_identity_and_creates_new_schedule_evidence(cohort):
    s = cohort
    generate_forecasts(s, now=NOW)
    schedule(s, at=NOW + timedelta(hours=1), kickoff=NOW + timedelta(days=4))
    assert generate_forecasts(s, now=NOW + timedelta(hours=1))["created"] == 3
    assert {p.game_id for p in s.scalars(select(Prediction))} == {GAME}
    assert len(list(s.scalars(select(Prediction)))) == 6


def test_earlier_revised_kickoff_keeps_older_eligible_forecast_in_performance(cohort):
    s=cohort
    generate_forecasts(s,now=NOW)
    s.add(ResearchSource(id="changed",dataset="injuries",season=2020,provider="nflverse",payload_hash="x",
        source_url="https://example.test",published_at=None,captured_at=NOW+timedelta(hours=2),coverage={}))
    s.flush()
    generate_forecasts(s,now=NOW+timedelta(hours=2))
    finaltime=NOW+timedelta(days=3)
    schedule(s,status="FINAL",at=finaltime,kickoff=NOW+timedelta(hours=1),summary="final 24-17 from nflverse")
    assert evaluate_forecasts(s,now=finaltime)["evaluations_added"]==3
    report=read_prospective(s,now=finaltime)
    assert report["performance"] and all(row["n"]==1 for row in report["performance"])
    assert report["evaluated_forecasts"]==3
    assert sum(not row["eligible_pregame_record"] for row in report["records"])==3


def test_checkpoint_is_actual_due_window_not_backfilled():
    kickoff = NOW + timedelta(hours=20)
    assert checkpoint(kickoff, NOW, CHECKPOINTS) == "PROS_24H"
    assert checkpoint(NOW, NOW, CHECKPOINTS) is None
    with pytest.raises(ValueError):
        checkpoint(kickoff, NOW, {"PROS_EARLY": float("nan")})


def test_ambiguous_cross_provider_candidate_list_blocks_matching_canonical_game(cohort):
    cohort.add(
        GameIdentityReview(
            id="foreign-ambiguous",
            data_mode="LIVE_RESEARCH",
            provider="other",
            provider_game_id="not-current-provider",
            reason="AMBIGUOUS_MATCH",
            evidence={"candidates": [GAME]},
            created_at=NOW,
        )
    )
    cohort.flush()
    assert generate_forecasts(cohort, now=NOW)["created"] == 0


def test_capture_failure_cannot_publish_new_forecasts_and_model_failure_rolls_back(cohort, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.models import Job
    from fde_api.forward import prospective

    factory = sessionmaker(bind=cohort.get_bind())
    result = prospective.run_forecast_cycle(factory, checkpoints=CHECKPOINTS, capture_succeeded=False)
    assert result["forecasts"]["created"] == 0

    def broken(*args, **kwargs):
        raise ValueError("controlled fit failure")

    monkeypatch.setattr(prospective, "generate_forecasts", broken)
    result = prospective.run_forecast_cycle(factory, checkpoints=CHECKPOINTS, capture_succeeded=True)
    assert result["status"] == "failed"
    cohort.expire_all()
    job = cohort.get(Job, result["run_id"])
    assert job.status == "failed" and "rolled back" in job.error
    assert list(cohort.scalars(select(Prediction))) == []
