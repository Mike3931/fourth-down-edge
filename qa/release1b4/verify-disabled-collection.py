"""Live acceptance: run from apps/api with uv run python ../../qa/release1b4/verify-disabled-collection.py.

Writes only the existing attested prospective acceptance workspace and a new
evidence JSON. Original profile and retained study are opened read-only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps/api"
TARGET = API / "data/research_1b4_final_20261014.db"
PROTECTED = [Path.home() / "NFL/apps/api/data/fde.db", API / "data/research_1b3_ordered_20261014.db"]
TABLES = ["predictions", "feature_snapshots", "model_versions", "model_evaluations",
          "research_sources", "research_facts", "research_polls", "schedule_observations",
          "games", "game_identity_reviews", "research_player_bindings", "research_player_reviews"]


def sha(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def snapshot(path, tables):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return {table: {str(row[0]): hashlib.sha256(json.dumps(row, default=str).encode()).hexdigest()
                        for row in db.execute(f'SELECT * FROM "{table}"')} for table in tables}


def main():
    before = snapshot(TARGET, TABLES)
    protected_before = {str(p): sha(p) for p in PROTECTED}
    historical_before = snapshot(PROTECTED[0], ["model_evaluations", "forward_ledger"])
    assert {"1", "2", "3"} <= set(historical_before["forward_ledger"])
    raw = API / "data/research_1b4_final_20261014.research-data"
    artifacts = {str(p): sha(p) for directory in (raw / "raw", raw / "artifacts")
                 for p in directory.rglob("*") if p.is_file()}
    requests = Counter()
    denied = []
    original_send = httpx.Client._send_single_request

    def audited_send(self, request):
        host = request.url.host.lower()
        if host == "nfl.com" or host.endswith(".nfl.com"):
            denied.append(str(request.url))
            raise AssertionError("Unauthorized NFL.com request attempted")
        requests[host] += 1
        return original_send(self, request)

    # Observe every synchronous HTTPX request, including redirect hops. A
    # forbidden attempt fails acceptance even if a provider catches the error.
    httpx.Client._send_single_request = audited_send
    sys.path.insert(0, str(API / "scripts"))
    import run_research

    sys.argv = ["run_research.py", "--database-file", str(TARGET), "--prospective"]
    code = run_research.main()
    assert code == 0 and not denied, (code, denied)

    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.db.models import FeatureSnapshot, Job, Prediction
    from fde_api.forward.prospective import CHECKPOINTS, read_prospective, run_forecast_cycle

    factory = sessionmaker(bind=get_engine(f"sqlite:///{TARGET.as_posix()}"))
    after_worker = snapshot(TARGET, TABLES)
    retry = run_forecast_cycle(factory, checkpoints=CHECKPOINTS, capture_succeeded=True)
    assert retry["status"] == "finished" and retry["forecasts"]["created"] == 0, retry
    after = snapshot(TARGET, TABLES)
    preservation = {}
    for table, rows in before.items():
        assert all(after[table].get(key) == value for key, value in rows.items()), table
        preservation[table] = {"before": len(rows), "after": len(after[table]), "all_prior_rows_unchanged": True}
    assert after_worker["predictions"] == after["predictions"]
    protected_after = {str(p): sha(p) for p in PROTECTED}
    assert protected_before == protected_after
    assert historical_before == snapshot(PROTECTED[0], ["model_evaluations", "forward_ledger"])
    assert all(sha(Path(p)) == value for p, value in artifacts.items())
    with factory() as s:
        view = read_prospective(s, now=datetime.now(UTC))
        assert view["decision"] == "NO BET"
        new_ids = set(after["predictions"]) - set(before["predictions"])
        assert new_ids, "Real worker did not publish new forecasts"
        predictions = list(s.scalars(select(Prediction).where(Prediction.id.in_(new_ids))))
        for p in predictions:
            frozen = s.get(FeatureSnapshot, p.feature_snapshot_id)
            assert all(t["availability_complete"] is False for t in frozen.values["research"]["teams"])
            assert p.created_at < datetime.fromisoformat(frozen.values["kickoff_at_creation"])
            assert p.outputs["research_only"] and p.outputs["approval_status"] == "research_only"
        jobs = [{"id": j.id, "kind": j.kind, "status": j.status, "result": j.result}
                for j in s.scalars(select(Job).order_by(Job.created_at.desc()).limit(3))]
        selected = next(r for r in view["records"] if r["id"] in new_ids)
    with sqlite3.connect(TARGET.as_uri() + "?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        official = db.execute("SELECT count(*) FROM research_polls WHERE dataset='official_injuries'").fetchone()[0]
    assert integrity == "ok" and not foreign_keys
    evidence = {"captured_at": datetime.now(UTC).isoformat(), "workspace": str(TARGET),
                "request_counts_by_host": dict(requests), "nfl_com_request_attempts": denied,
                "worker_exit_code": code, "jobs": jobs, "new_forecasts": len(new_ids),
                "retry": retry, "preservation": preservation, "selected_new_forecast": selected,
                "official_poll_count_after": official, "official_availability_complete": False,
                "decision": view["decision"], "evaluated_forecasts": view["evaluated_forecasts"],
                "protected_database_hashes_before": protected_before,
                "protected_database_hashes_after": protected_after,
                "all_original_evaluations_unchanged": True,
                "unresolved_forward_ledger_rows_unchanged": [1, 2, 3],
                "retained_artifact_files_unchanged": len(artifacts),
                "integrity": integrity, "foreign_key_violations": foreign_keys}
    output = Path(__file__).with_name("disabled-collection-evidence.json")
    output.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({"evidence": str(output), "new_forecasts": len(new_ids), "retry": retry,
                      "request_counts": dict(requests), "protected": protected_after}))


if __name__ == "__main__":
    main()
