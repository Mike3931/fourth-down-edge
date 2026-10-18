"""The normal bounded worker cannot silently contact restricted ESPN pages/APIs."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("cycles,interval", [(0, 300), (97, 300), (1, 299)])
def test_worker_keeps_bounded_cycle_and_polling_limits(tmp_path, monkeypatch, cycles, interval):
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_research.py"
    spec = importlib.util.spec_from_file_location("bounded_worker", script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    target = tmp_path / "not-adopted.db"
    target.write_bytes(b"existing file must remain untouched")
    monkeypatch.setattr(sys, "argv", [str(script), "--database-file", str(target),
                                     "--cycles", str(cycles), "--interval", str(interval)])
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 2
    assert target.read_bytes() == b"existing file must remain untouched"


def test_lock_is_not_bypassed_and_explicit_isolated_recovery_releases_it(tmp_path, monkeypatch):
    import sys

    target = tmp_path / "fresh.db"
    initialize_workspace(target, purpose="PROSPECTIVE")
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_research.py"
    spec = importlib.util.spec_from_file_location("locked_worker", script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(sys, "argv", [str(script), "--database-file", str(target), "--prospective"])
    lock = target.with_name(target.name + ".research.lock")
    lock.write_text("controlled interrupted process; no real process exists")
    calls = []
    monkeypatch.setattr(worker, "run", lambda *a: calls.append(True) or 0)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 2 and not calls and lock.exists()
    # Only this disposable fixture has no process. A real lock must never be
    # removed without checking the prior owner's actual process status.
    lock.unlink()
    assert worker.main() == 0 and calls == [True] and not lock.exists()
