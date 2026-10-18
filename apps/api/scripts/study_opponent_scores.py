"""Offline exploratory study in a NEW attested workspace; never touches live data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-file", required=True, type=Path)
    parser.add_argument("--licensed-games-file", required=True, type=Path)
    parser.add_argument("--source-receipt", required=True, type=Path)
    args = parser.parse_args()
    target = args.database_file.resolve()
    if target.exists() or target.name.lower() == "fde.db":
        parser.error("NEW isolated database required; existing databases are never adopted")
    payload = args.licensed_games_file.read_bytes()
    source_hash = hashlib.sha256(payload).hexdigest()
    receipt = json.loads(args.source_receipt.read_text())
    if (
        source_hash != receipt.get("sha256")
        or receipt.get("license") != "CC-BY-4.0"
        or receipt.get("url")
        != "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
    ):
        parser.error("Hash-verified licensed nflverse schedule receipt required")
    from fde_api.forward.workspace import initialize_workspace

    initialize_workspace(target)
    os.environ["FDE_DATABASE_URL"] = f"sqlite:///{target.as_posix()}"
    from sqlalchemy.orm import Session
    from threadpoolctl import threadpool_limits

    from fde_api.backtest.opponent_study import study
    from fde_api.backtest.research_comparison import run_comparison
    from fde_api.backtest.walkforward import load_market_refs
    from fde_api.canonical.load_games import load_games_csv
    from fde_api.config import settings
    from fde_api.db.engine import get_engine
    from fde_api.features.history import LeagueHistory

    settings.database_url = os.environ["FDE_DATABASE_URL"]
    settings.data_dir = target.with_suffix(".research-data")
    settings.artifacts_dir = settings.data_dir / "artifacts"
    settings.raw_dir = settings.data_dir / "raw"
    settings.reports_dir = settings.data_dir / "reports"
    settings.ensure_dirs()
    (settings.raw_dir / "licensed-games.csv").write_bytes(payload)
    shutil.copyfile(args.source_receipt, settings.raw_dir / "source-receipt.json")
    now = datetime.now(UTC)
    run_id = uuid.uuid4().hex[:10]
    # No 2026 results are loaded or consulted for model selection.
    engine = get_engine(settings.database_url)
    with Session(engine) as s, threadpool_limits(limits=1):
        load_games_csv(s, payload, seasons=list(range(2010, 2026)), source_manifest_version=source_hash)
        s.commit()
        try:
            original = run_comparison(
                s, test_seasons=[2022, 2023, 2024, 2025], source_hash=source_hash, now=now
            )
            s.commit()
            (settings.reports_dir / "baseline-reproduction.json").write_text(
                json.dumps(original, indent=2, allow_nan=False), encoding="utf-8"
            )
            history = LeagueHistory.load(s)
            market, _ = load_market_refs(s)
            result = study(
                s,
                history,
                market,
                seasons=[2022, 2023, 2024, 2025],
                source_hash=source_hash,
                run_id=run_id,
                now=now,
            )
            s.commit()
        except Exception as exc:
            s.rollback()
            (settings.reports_dir / f"failure-{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "source_hash": source_hash,
                        "status": "FAILED_RETAINED",
                    }
                ),
                encoding="utf-8",
            )
            raise
    rows = result.pop("rows")
    with (settings.reports_dir / "per-game.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (settings.reports_dir / "comparison.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "report": str(settings.reports_dir / "comparison.json"),
                "status": result["status"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
