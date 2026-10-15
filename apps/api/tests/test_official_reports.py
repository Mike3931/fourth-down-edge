from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from fde_api.db.models import Player
from fde_api.db.research_models import PlayerBinding, ResearchFact, ResearchPoll
from fde_api.forward.official_reports import collect_official, ingest_official
from test_prospective import NOW, cohort  # noqa: F401

HTML = b"""<h1>2020 NFL Injury Report</h1><h2>Injuries - WEEK 6</h2><span>Seahawks</span>
<table><tr><td><a href="/players/test-player/">Test player</a></td><td>RB</td><td>Knee</td><td>Limited Participation in Practice</td><td>Out</td></tr></table>"""


def test_official_page_preserves_unmapped_out_without_inactive_or_issue_time(cohort):  # noqa: F811
    s = cohort
    source = ingest_official(s, HTML, captured_at=NOW, season=2020)
    assert source.published_at is None and not source.coverage["complete_availability"]
    rows = list(s.scalars(select(ResearchFact).where(ResearchFact.source_id == source.id)))
    assert len(rows) == 1 and rows[0].player_id is None
    assert rows[0].canonical_game_id == "2020_06_SEA_DEN"
    assert rows[0].values["designation"] == "Out" and not rows[0].values["confirmed_inactive"]
    assert ingest_official(s, HTML, captured_at=NOW + timedelta(minutes=1), season=2020).id == source.id
    s.add(
        PlayerBinding(
            provider="nfl",
            provider_id="test-player",
            player_id="00-0000001",
            observed_at=NOW,
            evidence={"review": "explicit"},
        )
    )
    s.flush()
    # An NFL slug alone cannot partially publish a canonical player identity.
    assert ingest_official(s,HTML,captured_at=NOW+timedelta(minutes=1),season=2020).id==source.id
    s.add(Player(id="00-0000001",name="Explicit canonical test player"))
    s.add(PlayerBinding(provider="gsis",provider_id="00-0000001",player_id="00-0000001",
        observed_at=NOW,evidence={"review":"explicit GSIS identity"}))
    s.flush()
    mapped = ingest_official(s, HTML, captured_at=NOW + timedelta(minutes=2), season=2020)
    assert mapped.id != source.id and mapped.coverage["mapped_players"] == 1
    assert s.get(ResearchFact, rows[0].id).player_id is None


def test_wrong_week_layout_and_season_refused(cohort):  # noqa: F811
    with pytest.raises(ValueError):
        ingest_official(cohort, HTML, captured_at=NOW, season=2098)
    with pytest.raises(ValueError):
        ingest_official(cohort, b"<html>Unavailable</html>", captured_at=NOW, season=2020)


def test_official_outage_is_audited_without_erasing_evidence(cohort, monkeypatch):  # noqa: F811
    # Exercise the retained transport with a synthetic response only. Production
    # has no authorization/configuration bypass.
    monkeypatch.setattr("fde_api.forward.official_reports.automated_retrieval_authorized", lambda: True)
    s = cohort
    source = ingest_official(s, HTML, captured_at=NOW, season=2020)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503, request=req)))
    assert collect_official(s, season=2020, now=NOW, client=client)["official_failures"] == 1
    assert s.get(type(source), source.id) is not None
    assert s.scalars(select(ResearchPoll)).first().status == "FAILED"
    assert collect_official(s, season=2020, now=NOW + timedelta(minutes=1), client=client) == {
        "official_not_due": 1
    }


@pytest.mark.parametrize("injected_client", [False, True])
def test_unauthorized_collection_never_contacts_nfl_or_changes_evidence(cohort, monkeypatch, injected_client):  # noqa: F811
    from fde_api.db.models import Prediction
    from fde_api.forward.prospective import generate_forecasts

    s = cohort
    source = ingest_official(s, HTML, captured_at=NOW, season=2020)
    s.commit()
    original = [(r.id, r.values.copy()) for r in s.scalars(select(ResearchFact))]
    assert generate_forecasts(s, now=NOW)["created"] == 3
    s.commit()
    forecasts = {p.id: p.outputs.copy() for p in s.scalars(select(Prediction))}

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled collector attempted HTTP access")

    client = httpx.Client(transport=httpx.MockTransport(forbidden)) if injected_client else None
    monkeypatch.setattr(httpx, "Client", forbidden)
    # Neither a guessed opt-in nor a successful HTTP-capable client grants rights.
    monkeypatch.setenv("FDE_NFL_OFFICIAL_ENABLED", "true")
    monkeypatch.setenv("FDE_NFL_OFFICIAL_AUTHORIZED", "true")
    for at in (NOW, NOW + timedelta(hours=1)):
        assert collect_official(s, season=2020, now=at, client=client) == {
            "official_disabled_pending_authorization": 1
        }
    assert not list(s.scalars(select(ResearchPoll)))
    assert [(r.id, r.values) for r in s.scalars(select(ResearchFact))] == original
    assert s.get(type(source), source.id).coverage["complete_availability"] is False
    assert generate_forecasts(s, now=NOW + timedelta(minutes=1))["created"] == 0
    assert {p.id: p.outputs for p in s.scalars(select(Prediction))} == forecasts
    # The disabled source cannot unlock the final checkpoint.
    assert generate_forecasts(s, now=NOW + timedelta(hours=47))["created"] == 0
    if client:
        client.close()


@pytest.mark.parametrize("skip_flag", [False, True])
def test_standard_prospective_worker_does_not_request_official_pages(cohort, monkeypatch, tmp_path, capsys, skip_flag):  # noqa: F811
    import argparse
    import importlib
    import json
    from pathlib import Path

    from fde_api.config import settings

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    worker = importlib.import_module("run_research")
    espn = importlib.import_module("espn_live_capture")
    # Only independent providers are stubbed; exercise the worker's real
    # official collector entry point with all HTTP constructors forbidden.
    monkeypatch.setattr(espn, "fetch", lambda window: {})
    monkeypatch.setattr(espn, "discover", lambda payload: [])
    monkeypatch.setattr(espn, "within_utc_window", lambda games, window: games)
    monkeypatch.setattr(espn, "persist", lambda *a, **kw: {"identity_refused": 0})
    monkeypatch.setattr("fde_api.db.engine.get_engine", lambda url: cohort.get_bind())
    monkeypatch.setattr("fde_api.forward.workspace.authorize_database", lambda *a, **kw: None)
    monkeypatch.setattr("fde_api.forward.intelligence.collect_intelligence", lambda *a, **kw: {"research_failures": 0})
    monkeypatch.setattr("fde_api.forward.research_weather.collect_weather", lambda *a, **kw: {})
    monkeypatch.setattr("fde_api.forward.research_runner.run_cycle",
                        lambda factory, capture, **kw: {"status": "finished", "capture": capture()})
    monkeypatch.setattr("fde_api.forward.prospective.run_forecast_cycle",
                        lambda factory, **kw: {"status": "finished", "capture_succeeded": kw["capture_succeeded"]})
    for name in ("database_url", "data_dir", "raw_dir", "artifacts_dir", "reports_dir"):
        monkeypatch.setattr(settings, name, getattr(settings, name))
    monkeypatch.setenv("FDE_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("FDE_DATA_DIR", str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("Standard worker attempted disabled official-page HTTP access")

    monkeypatch.setattr(httpx, "Client", forbidden)
    args = argparse.Namespace(cycles=1, prospective=True, season=2020,
                              checkpoint_hours=[168, 72, 24, 1.5], skip_official_capture=skip_flag)
    assert worker.run(args, tmp_path / "isolated.db") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["capture"]["official_disabled_pending_authorization"] == 1
    assert result["prospective"]["capture_succeeded"] is True
