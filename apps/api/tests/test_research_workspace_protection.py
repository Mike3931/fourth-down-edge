import json
import os
import shutil
import sqlite3

import pytest

from fde_api.forward.workspace import authorize_database, initialize_workspace


def test_only_new_initialized_workspace_is_writable(tmp_path, monkeypatch):
    monkeypatch.delenv("FDE_PROTECTED_DATABASES", raising=False)
    path = tmp_path / "isolated.db"
    initialize_workspace(path)
    authorize_database(path)
    with pytest.raises(FileExistsError):
        initialize_workspace(path)
    copy = tmp_path / "renamed-copy.db"
    shutil.copyfile(path, copy)
    shutil.copyfile(path.with_name(path.name + ".research-workspace.json"),
                    copy.with_name(copy.name + ".research-workspace.json"))
    with pytest.raises(ValueError, match="file identity"):
        authorize_database(copy)
    link = tmp_path / "linked-profile.db"
    os.link(path, link)
    monkeypatch.setenv("FDE_PROTECTED_DATABASES", str(path))
    with pytest.raises(ValueError, match="Protected database identity"):
        authorize_database(link)


def test_renamed_unregistered_populated_profile_is_not_adopted(tmp_path):
    path = tmp_path / "innocent-name.db"
    path.write_bytes(b"existing protected contents")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        authorize_database(path)
    with pytest.raises(FileExistsError):
        initialize_workspace(path)
    assert path.read_bytes() == before


def test_workspace_token_mismatch_fails_read_only(tmp_path):
    path = tmp_path / "research.db"
    initialize_workspace(path)
    marker = path.with_name(path.name + ".research-workspace.json")
    data = json.loads(marker.read_text())
    data["workspace_id"] = "not-the-database-token"
    marker.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="identity mismatch"):
        authorize_database(path)
    assert path.read_bytes() == before


def test_historical_evaluations_block_even_an_attested_database(tmp_path):
    path = tmp_path / "isolated.db"
    initialize_workspace(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO model_evaluations VALUES ('frozen','model','test','{}',1,'2026-10-13')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Historical research"):
        authorize_database(path)
    assert path.read_bytes() == before


def test_cached_settings_cannot_redirect_worker_to_another_profile(tmp_path, monkeypatch):
    import argparse
    import importlib.util
    from pathlib import Path

    from fde_api.config import settings

    monkeypatch.setenv("FDE_DATABASE_URL", "sqlite:///unused-cached-profile.db")
    monkeypatch.setenv("FDE_DATA_DIR", str(tmp_path / "unused-cached-data"))
    target = tmp_path / "workspace.db"
    initialize_workspace(target)
    protected = tmp_path / "renamed-original.db"
    protected.write_bytes(b"do not open this profile")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{protected.as_posix()}")
    for attribute in ("data_dir", "raw_dir", "artifacts_dir", "reports_dir"):
        monkeypatch.setattr(settings, attribute, tmp_path / "wrong-profile")
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_research.py"
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location("worker_identity_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import espn_live_capture

    monkeypatch.setattr(espn_live_capture, "fetch", lambda _: {"events": []})
    from fde_api.forward import intelligence
    monkeypatch.setattr(intelligence, "collect_intelligence", lambda *a, **kw: {"research_failures": 0})
    assert module.run(argparse.Namespace(cycles=1, interval=300), target) == 0
    assert protected.read_bytes() == b"do not open this profile"
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
