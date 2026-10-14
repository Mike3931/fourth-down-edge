"""Capture genuine NFL data and evaluate existing singles models in a NEW workspace.

Never adopts/migrates an existing database. Every invocation retains its own
sources, model registry, predictions, evaluations, failures and report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-file", type=Path, required=True)
    parser.add_argument("--test-seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    parser.add_argument("--capture-current", action="store_true")
    args = parser.parse_args()
    target = args.database_file.resolve()
    if target.name.lower() == "fde.db" or target.exists():
        parser.error("Use a NEW isolated database; existing databases are never adopted")
    if args.test_seasons != sorted(set(args.test_seasons)) or any(y < 2012 or y > 2025 for y in args.test_seasons):
        parser.error("Ascending distinct completed test seasons 2012–2025 required")
    from fde_api.forward.workspace import initialize_workspace

    initialize_workspace(target)
    os.environ["FDE_DATABASE_URL"] = f"sqlite:///{target.as_posix()}"
    from sqlalchemy.orm import Session

    from fde_api.backtest.research_comparison import render_markdown, run_comparison
    from fde_api.canonical.load_games import load_games_csv
    from fde_api.config import settings
    from fde_api.db.engine import get_engine
    from fde_api.db.models import BacktestRun
    from fde_api.ingest import ingest_dataset
    from fde_api.providers import NflverseProvider
    from fde_api.raw import RawArtifactStore

    settings.database_url = os.environ["FDE_DATABASE_URL"]
    settings.data_dir = target.with_suffix(".research-data")
    settings.raw_dir = settings.data_dir / "raw"
    settings.artifacts_dir = settings.data_dir / "artifacts"
    settings.reports_dir = settings.data_dir / "reports"
    settings.ensure_dirs()
    engine = get_engine(settings.database_url)
    store = RawArtifactStore(settings.raw_dir)
    with Session(engine) as s:
        manifest = ingest_dataset(s, NflverseProvider(), "games", {}, store)
        payload = store.artifact_path(manifest).read_bytes()
        source_hash = hashlib.sha256(payload).hexdigest()
        load_games_csv(s, payload, seasons=list(range(2010, datetime.now(UTC).year + 1)),
                       source_manifest_version=manifest.version_id)
        s.commit()
    if args.capture_current:
        # Capture before adding historical study records; the existing worker
        # deliberately refuses further writes once a workspace holds evaluations.
        subprocess.run([sys.executable, str(Path(__file__).with_name("run_research.py")),
                        "--database-file", str(target), "--season", str(datetime.now(UTC).year)], check=True)
    with Session(engine) as s:
        try:
            report = run_comparison(s, test_seasons=args.test_seasons, source_hash=source_hash,
                                    now=datetime.now(UTC))
            s.commit()
        except Exception as exc:
            s.rollback()
            failure = BacktestRun(id="cmp_failed_" + uuid.uuid4().hex[:12],
                config={"test_seasons": args.test_seasons, "source_hash": source_hash},
                status="failed", error=f"{type(exc).__name__}: {exc}", created_at=datetime.now(UTC))
            s.add(failure)
            s.commit()
            report = {"run_id": failure.id, "status": "VALIDATION FAILED", "error": failure.error}
        output = settings.reports_dir / f"{report['run_id']}.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        if "error" not in report:
            with output.with_suffix(".md").open("x", encoding="utf-8") as handle:
                handle.write(render_markdown(report))
        print(json.dumps({"database": str(target), "report": str(output),
                          "run_id": report["run_id"], "status": report["status"]}))
        return 0 if "error" not in report else 2


if __name__ == "__main__":
    raise SystemExit(main())
