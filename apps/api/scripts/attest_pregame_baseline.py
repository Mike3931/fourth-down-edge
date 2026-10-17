"""Adopt an independently retained pregame baseline, with an explicitly trusted hash.

Never rewrites predictions or pretends this import happened before kickoff.
Missing earlier forecast versions remain un-attested and ineligible.
"""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from fde_api.db.engine import get_engine
from fde_api.db.models import FeatureSnapshot, ForecastFreeze, ModelVersion, Prediction
from fde_api.forward.forecast_integrity import graph_hash
from fde_api.forward.workspace import authorize_database


def attest(target, baseline, expected_hash):
    authorize_database(target, prospective=True)
    actual = hashlib.sha256(baseline.read_bytes()).hexdigest()
    if actual != expected_hash:
        raise ValueError("Baseline hash does not match trusted pregame digest")
    data = json.loads(baseline.read_text(encoding="utf-8"))
    attested = datetime.fromisoformat(data["record_created_at"])
    kickoff = datetime.fromisoformat(data["kickoff_utc"])
    if attested >= kickoff or not data["created_before_kickoff"]:
        raise ValueError("Baseline was not recorded pregame")
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for r in data["records"]:
            for table, pk, field in [
                ("predictions", r["forecast_id"], "forecast_row_sha256"),
                ("feature_snapshots", r["feature_snapshot_id"], "feature_row_sha256"),
                ("model_versions", r["model_version"], "model_row_sha256"),
            ]:
                row = db.execute(f"SELECT * FROM {table} WHERE id=?", (pk,)).fetchone()
                if (
                    row is None
                    or hashlib.sha256(
                        json.dumps(dict(row), sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    != r[field]
                ):
                    raise ValueError(f"Frozen {table} row hash mismatch: {pk}")
    created = 0
    with Session(get_engine("sqlite:///" + target.as_posix())) as s:
        for r in data["records"]:
            p = s.get(Prediction, r["forecast_id"])
            f = s.get(FeatureSnapshot, r["feature_snapshot_id"])
            m = s.get(ModelVersion, r["model_version"])
            if (
                p.game_id != data["game_id"]
                or p.feature_snapshot_id != f.id
                or p.model_version_id != m.id
                or p.created_at > attested
            ):
                raise ValueError("Baseline graph identity or timestamp mismatch")
            h = graph_hash(p, f, m)
            old = s.get(ForecastFreeze, p.id)
            if old:
                if old.graph_hash != h:
                    raise ValueError("Conflicting attestation")
                continue
            s.add(
                ForecastFreeze(
                    prediction_id=p.id,
                    graph_hash=h,
                    attested_at=attested,
                    recorded_at=datetime.now(UTC),
                    provenance=f"Retained pregame baseline SHA256 {actual}; {baseline.name}",
                )
            )
            created += 1
        s.commit()
    return {"attestations_added": created, "baseline_sha256": actual, "attested_at": attested.isoformat()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-file", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(attest(args.database_file.resolve(), args.baseline.resolve(), args.expected_sha256)))
