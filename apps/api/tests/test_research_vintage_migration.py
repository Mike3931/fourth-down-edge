import sqlite3

import polars as pl
import pytest
from sqlalchemy import select

from fde_api.canonical.load_rosters import load_rosters_parquet
from fde_api.db.models import Player, RosterEntry, Team
from fde_api.forward.workspace import initialize_workspace


def test_append_only_triggers_on_actual_sqlite_migration(tmp_path):
    target = tmp_path / "new.db"
    initialize_workspace(target)
    with sqlite3.connect(target) as db:
        assert db.execute("select version_num from alembic_version").fetchone()[0] == "f13b2a9c8041"
        db.execute("INSERT INTO research_player_bindings VALUES ('espn','ID','GSIS','2026-10-13','{}')")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE research_player_bindings SET player_id='CHANGED'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM research_player_bindings")


def test_legacy_loader_repeat_preserves_reference_state_and_other_seasons(session, tmp_path):
    session.add(Team(id="KC", name="Kansas City"))
    session.flush()
    path = tmp_path / "rosters.parquet"
    row = {"season": 2026, "team": "KC", "position": "QB", "status": "ACT",
           "full_name": "Original", "gsis_id": "00-0000001", "birth_date": "1995-01-01", "rookie_year": 2020}
    pl.DataFrame([row, {**row, "season": 2025, "gsis_id": "00-0000002"}]).write_parquet(path)
    load_rosters_parquet(session, path, season=2026)
    session.flush()
    pl.DataFrame([{**row, "position": "WR", "full_name": "Changed", "status": "IR"}]).write_parquet(path)
    load_rosters_parquet(session, path, season=2026)
    session.flush()
    assert session.get(Player, "00-0000001").name == "Original"
    assert session.get(Player, "00-0000002") is None
    entries = list(session.scalars(select(RosterEntry)))
    assert len(entries) == 1 and entries[0].position == "QB" and entries[0].observed_at is None
