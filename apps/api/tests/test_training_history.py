"""Controlled historical rows cannot become manufactured prospective evidence."""
import csv
import io
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from fde_api.db.forward_models import GameIdentityReview, ScheduleObservation
from fde_api.db.models import Prediction
from fde_api.db.research_models import ResearchSource
from fde_api.forward.intelligence import collect_intelligence
from fde_api.forward.prospective import generate_forecasts, schedule_at
from fde_api.forward.training_history import FIELDS, capture_training_history
from fde_api.raw import RawArtifactStore
from test_prospective import NOW, cohort, schedule  # noqa: F401


def payload(*, score="24", current_score="", odds="-110"):
    teams = ["SEA", "ATL", "ARI", "BUF", "BAL", "CAR", "CHI", "CLE", "CIN", "DAL",
             "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[*FIELDS, "moneyline"])
    writer.writeheader()
    for year, kind, week in [(2019, "REG", n) for n in range(1, 18)] + [
        (2018, "REG", n) for n in range(1, 18)
    ] + [(2019, "PRE", 1), (2019, "WC", 18), (2020, "REG", 1)]:
        away = teams[(week - 1) % len(teams)]
        writer.writerow({"game_id": f"{year}_{week:02}_{away}_DEN", "season": year, "game_type": kind,
            "week": week, "home_team": "DEN", "away_team": away, "gameday": f"{year}-09-01", "gametime": "13:00",
            "home_score": current_score if year == 2020 else str(int(score) + week % 9),
            "away_score": str(10 + week % 12), "moneyline": odds})
    return out.getvalue().encode()


def test_history_capture_time_deduplication_and_early_season_training(cohort, tmp_path):  # noqa: F811
    s = cohort
    # Remove only the controlled fixture's artificial finals: no current-season training remains.
    for row in s.scalars(select(ScheduleObservation).where(ScheduleObservation.game_status == "FINAL")):
        s.delete(row)
    s.flush()
    assert generate_forecasts(s, now=NOW)["created"] == 0
    store = RawArtifactStore(tmp_path / "history")
    source = capture_training_history(s, payload=payload(), season=2020, captured_at=NOW, store=store)
    assert source.coverage["source_rows"] == 34 and source.published_at is None
    assert source.coverage["results_recorded"] == 34
    assert len(schedule_at(s, NOW - timedelta(seconds=1))[0]) == 0
    states = schedule_at(s, NOW)[0]
    assert sum(g.game_status == "FINAL" for g in states.values()) == 34
    assert not any(g.season_type != "REG" for g in states.values())
    before = s.scalar(select(func.count()).select_from(ScheduleObservation))
    again = capture_training_history(s, payload=payload(current_score="30", odds="+200"),
        season=2020, captured_at=NOW + timedelta(minutes=1), store=store)
    assert again.id == source.id and again.captured_at == NOW
    assert s.scalar(select(func.count()).select_from(ScheduleObservation)) == before
    result = generate_forecasts(s, now=NOW)
    assert result["training_games"] == 34 and result["created"] == 3
    # Historical imports provide training, never historical predictions.
    assert {p.game_id for p in s.scalars(select(Prediction))} == {"2020_06_SEA_DEN"}


def test_historical_score_revision_keeps_evidence_and_requires_review(cohort, tmp_path):  # noqa: F811
    s = cohort
    store = RawArtifactStore(tmp_path / "history")
    first = capture_training_history(s, payload=payload(), season=2020, captured_at=NOW, store=store)
    original = {g.id: (g.content_hash, g.change_summary) for g in s.scalars(select(ScheduleObservation))}
    revision = capture_training_history(s, payload=payload(score="27"), season=2020,
        captured_at=NOW + timedelta(minutes=1), store=store)
    assert revision.id != first.id and revision.coverage["results_refused"] == 34
    for identity, values in original.items():
        row = s.get(ScheduleObservation, identity)
        assert (row.content_hash, row.change_summary) == values
    assert len(list(s.scalars(select(GameIdentityReview).where(
        GameIdentityReview.reason == "RESULT_CONFLICT_OR_INVALID")))) == 34
    assert first.coverage["results_recorded"] == 34


def test_prospective_worker_captures_history_from_same_download(cohort, monkeypatch):  # noqa: F811
    import fde_api.forward.intelligence as intelligence

    monkeypatch.setattr(intelligence, "INTERVALS", {"schedule": 900})
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, content=payload(), request=request)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        counts = collect_intelligence(cohort, now=NOW, season=2020, client=client,
                                      clock=lambda: NOW, include_history=True)
    assert len(requests) == 1 and counts["research_failures"] == 0
    sources = list(cohort.scalars(select(ResearchSource)))
    assert {src.dataset for src in sources} == {"schedule", "schedule_history"}
    assert next(src for src in sources if src.dataset == "schedule_history").coverage["results_recorded"] == 34
