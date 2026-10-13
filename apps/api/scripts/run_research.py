"""Bounded free-source research worker; explicit isolated DB, no auto-installation.

Run from apps/api: uv run python scripts/run_research.py --database-file data/research.db
Migrate that disposable database separately first. Repeat with --cycles/--interval.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-file", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--interval", type=int, default=900)
    args = parser.parse_args()
    target = args.database_file.resolve()
    if target.name.lower() == "fde.db" or not target.is_file():
        parser.error("Use an existing migrated isolated database, never the original fde.db")
    if not 1 <= args.cycles <= 96 or args.interval < 300:
        parser.error("Maximum 96 cycles; minimum refresh interval 300 seconds")
    lock = target.with_name(target.name + ".research.lock")
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.error("Research worker lock exists; confirm the prior worker stopped before recovery")
    try:
        return run(args, target)
    finally:
        os.close(handle)
        lock.unlink()


def run(args: argparse.Namespace, target: Path) -> int:
    os.environ["FDE_DATABASE_URL"] = f"sqlite:///{target.as_posix()}"
    os.environ["FDE_DATA_DIR"] = str(target.parent / (target.stem + ".research-data"))
    from espn_live_capture import discover, fetch, persist, within_utc_window
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.forward.research_runner import run_cycle
    from fde_api.forward.research_weather import collect_weather

    factory = sessionmaker(bind=get_engine())

    exit_code = 0
    for cycle in range(args.cycles):
        now = datetime.now(UTC)
        # UTC slack includes today's Chicago evening games; the view uses local days.
        dates = f"{now - timedelta(days=1):%Y%m%d}-{now + timedelta(days=7):%Y%m%d}"
        def capture(window: str = dates) -> dict[str, int]:
            games = within_utc_window(discover(fetch(window)), window)
            counts = persist(games, data_mode_value="LIVE_RESEARCH")
            with factory() as s:
                counts.update(collect_weather(s, now=datetime.now(UTC)))
                s.commit()
            return counts

        result = run_cycle(factory, capture, dates=dates)
        print(json.dumps(result))
        if result["status"] == "failed":
            exit_code = 2
        if cycle + 1 < args.cycles:
            time.sleep(args.interval)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
