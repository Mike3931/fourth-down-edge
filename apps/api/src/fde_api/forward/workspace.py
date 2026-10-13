"""Explicit fresh-workspace capability. Copies, links and renamed profiles fail closed."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


def authorize_database(target: Path) -> None:
    target = target.resolve()
    protected = [Path.home() / "NFL/apps/api/data/fde.db"]
    protected += [Path(p) for p in os.environ.get("FDE_PROTECTED_DATABASES", "").split(os.pathsep) if p]
    if target.name.lower() == "fde.db" or any(p.exists() and target.samefile(p) for p in protected):
        raise ValueError("Protected database identity")
    marker = target.with_name(target.name + ".research-workspace.json")
    try:
        attestation = json.loads(marker.read_text())
        stat = target.stat()
        if [stat.st_dev, stat.st_ino] != attestation["file_identity"]:
            raise ValueError("Database file identity differs from the authorized workspace")
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
            tokens = [row[0] for row in db.execute("SELECT id FROM research_workspace")]
            if tokens != [attestation["workspace_id"]]:
                raise ValueError("Workspace identity mismatch")
            for table in ("forward_ledger", "backtest_runs", "model_evaluations"):
                if db.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                    raise ValueError("Historical research records prohibit worker writes")
    except (OSError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
        raise ValueError("Missing or invalid fresh-workspace authorization") from exc


def initialize_workspace(target: Path) -> None:
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never adopt a populated DB, even if it is renamed or its filename seems safe.
    handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    database_url = f"sqlite:///{target.as_posix()}"
    from sqlalchemy.orm import Session

    from fde_api.db.engine import get_engine
    from fde_api.db.forward_models import ResearchWorkspace

    root = Path(__file__).resolve().parents[3]
    # A fresh process avoids cached settings accidentally selecting another DB.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root,
                   env={**os.environ, "FDE_DATABASE_URL": database_url},
                   check=True, capture_output=True)
    token = str(uuid.uuid4())
    with Session(get_engine(database_url)) as s:
        s.add(ResearchWorkspace(id=token, created_at=datetime.now(UTC)))
        s.commit()
    stat = target.stat()
    marker = target.with_name(target.name + ".research-workspace.json")
    with marker.open("x") as output:
        json.dump({"workspace_id": token, "file_identity": [stat.st_dev, stat.st_ino]}, output)
