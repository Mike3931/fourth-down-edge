from datetime import timedelta

from sqlalchemy import func, select

from fde_api.db.models import Job, Prediction
from fde_api.forward.prospective import generate_forecasts, read_prospective
from test_prospective import GAME, NOW, cohort  # noqa: F401


def states(result):
    return {r["horizon"]: r for r in result["checkpoint_statuses"]}


def test_unrecorded_selected_game_distinguishes_missed_due_and_future(cohort):  # noqa: F811
    result = states(read_prospective(cohort, now=NOW, game_id=GAME))
    assert result["PROS_EARLY"]["state"] == "MISSED"
    assert result["PROS_72H"]["state"] == "AWAITING_CAPTURE"
    assert result["PROS_24H"]["state"] == "NOT_YET_DUE"
    assert result["PROS_FINAL"]["state"] == "NOT_YET_DUE"
    assert cohort.scalar(select(func.count()).select_from(Prediction)) == 0


def test_recorded_checkpoint_is_read_only_and_final_availability_still_withholds(cohort):  # noqa: F811
    assert generate_forecasts(cohort, now=NOW)["created"] == 3
    cohort.commit()
    result = states(read_prospective(cohort, now=NOW, game_id=GAME))
    assert result["PROS_72H"]["state"] == "RECORDED"
    assert result["PROS_72H"]["recorded_approaches"] == 3
    final = states(read_prospective(cohort, now=NOW+timedelta(hours=47), game_id=GAME))
    assert final["PROS_FINAL"]["state"] == "WITHHELD"
    assert "official availability" in final["PROS_FINAL"]["reason"]
    assert cohort.scalar(select(func.count()).select_from(Prediction)) == 3


def test_current_stale_schedule_is_explicitly_withheld(cohort):  # noqa: F811
    result = states(read_prospective(cohort, now=NOW+timedelta(hours=30), game_id=GAME))
    assert result["PROS_24H"]["state"] == "WITHHELD"
    assert "stale" in result["PROS_24H"]["reason"]


def test_final_withholding_is_retained_after_window_closes_and_reschedules_remain_future(cohort):  # noqa: F811
    from test_prospective import schedule

    cohort.add(Job(id="withheld-final", kind="prospective_forecast", params={}, status="finished",
                   created_at=NOW+timedelta(hours=47),
                   result={"forecasts":{"withheld":[GAME+": final checkpoint requires complete official availability"]}}))
    cohort.commit()
    result = states(read_prospective(cohort, now=NOW+timedelta(hours=49), game_id=GAME))
    assert result["PROS_FINAL"]["state"] == "WITHHELD"
    assert "official availability" in result["PROS_FINAL"]["reason"]
    schedule(cohort, at=NOW+timedelta(hours=49), kickoff=NOW+timedelta(hours=96))
    cohort.commit()
    future = states(read_prospective(cohort, now=NOW+timedelta(hours=50), game_id=GAME))
    assert future["PROS_FINAL"]["state"] == "NOT_YET_DUE"
