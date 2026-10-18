"""The normal bounded worker cannot silently contact restricted ESPN pages/APIs."""
import argparse
import importlib.util
import json
from pathlib import Path

from fde_api.config import settings
from fde_api.forward.workspace import initialize_workspace


def test_worker_uses_licensed_schedule_and_never_fetches_espn(tmp_path, monkeypatch, capsys):
    target = tmp_path / "fresh.db"
    initialize_workspace(target, purpose="PROSPECTIVE")
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_research.py"
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location("weekend_worker", script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    import espn_live_capture

    def forbidden(*args, **kwargs):
        raise AssertionError("No documented ESPN automated-collection authorization")

    monkeypatch.setattr(espn_live_capture, "fetch", forbidden)
    monkeypatch.setattr(espn_live_capture, "persist", forbidden)
    for name in ("database_url", "data_dir", "raw_dir", "artifacts_dir", "reports_dir"):
        monkeypatch.setattr(settings, name, getattr(settings, name))
    monkeypatch.setattr("fde_api.forward.intelligence.collect_intelligence",
                        lambda *a, **kw: {"research_failures": 0})
    monkeypatch.setattr("fde_api.forward.research_weather.collect_weather", lambda *a, **kw: {})
    assert worker.run(argparse.Namespace(cycles=1, interval=300, prospective=True,
                                         checkpoint_hours=[168, 72, 24, 1.5]), target) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["capture"]["espn_disabled_pending_authorization"] == 1
    assert result["capture"]["official_disabled_pending_authorization"] == 1


def test_schedule_uses_the_licensed_release():
    from fde_api.forward.intelligence import source_url

    assert source_url("schedule", 2026) == (
        "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
    )
