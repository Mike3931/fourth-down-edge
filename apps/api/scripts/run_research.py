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
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--prospective", action="store_true", help="Requires a NEW explicitly prospective workspace")
    parser.add_argument("--skip-official-capture", action="store_true", help="Keep official coverage unknown; useful for source acceptance comparisons")
    parser.add_argument("--checkpoint-hours", type=float, nargs=4, default=[168, 72, 24, 1.5],
                        metavar=("EARLY", "H72", "H24", "FINAL"))
    args = parser.parse_args()
    target = args.database_file.resolve()
    if target.name.lower() == "fde.db" or not target.is_file():
        parser.error("Use an existing migrated isolated database, never the original fde.db")
    if not 1 <= args.cycles <= 96 or args.interval < 300:
        parser.error("Maximum 96 cycles; minimum refresh interval 300 seconds")
    from fde_api.forward.workspace import authorize_database

    try:
        authorize_database(target, prospective=args.prospective)
    except ValueError as exc:
        parser.error(str(exc))
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

    from fde_api.config import settings
    from fde_api.db.engine import get_engine
    from fde_api.forward.intelligence import collect_intelligence
    from fde_api.forward.official_reports import collect_official
    from fde_api.forward.prospective import CHECKPOINTS, run_forecast_cycle
    from fde_api.forward.research_runner import run_cycle
    from fde_api.forward.research_weather import collect_weather
    from fde_api.forward.workspace import authorize_database

    # Guard imports can initialize settings before CLI env overrides. Never let
    # a cached/default profile decide where this write-capable worker connects.
    settings.database_url = os.environ["FDE_DATABASE_URL"]
    settings.data_dir = Path(os.environ["FDE_DATA_DIR"])
    settings.raw_dir = settings.data_dir / "raw"
    settings.artifacts_dir = settings.data_dir / "artifacts"
    settings.reports_dir = settings.data_dir / "reports"
    factory = sessionmaker(bind=get_engine(settings.database_url))

    exit_code = 0
    for cycle in range(args.cycles):
        authorize_database(target, prospective=getattr(args, "prospective", False))
        now = datetime.now(UTC)
        # UTC slack includes today's Chicago evening games; the view uses local days.
        dates = f"{now - timedelta(days=1):%Y%m%d}-{now + timedelta(days=7):%Y%m%d}"
        def capture(window: str = dates, season: int = getattr(args, "season", None)
                    or (now.year if now.month >= 8 else now.year - 1)) -> dict[str, int]:
            games = within_utc_window(discover(fetch(window)), window)
            counts = persist(games, data_mode_value="LIVE_RESEARCH")
            if counts["identity_refused"]:
                raise ValueError("Game identity review required; no complete-slate claim")
            with factory() as s:
                counts.update(collect_intelligence(s, now=datetime.now(UTC), season=season))
                counts.update(collect_weather(s, now=datetime.now(UTC)))
                if getattr(args, "prospective", False) and not getattr(args, "skip_official_capture", False):
                    counts.update(collect_official(s, season=season, now=datetime.now(UTC)))
                s.commit()
            if counts["research_failures"]:
                raise ValueError("Research provider refresh failed; no current-source claim")
            return counts

        result = run_cycle(factory, capture, dates=dates)
        if getattr(args, "prospective", False):
            result["prospective"] = run_forecast_cycle(factory,
                checkpoints=dict(zip(CHECKPOINTS, args.checkpoint_hours, strict=True)),
                capture_succeeded=result["status"] == "finished")
            if result["prospective"]["status"] == "failed":
                exit_code = 2
        print(json.dumps(result))
        if result["status"] == "failed":
            exit_code = 2
        if cycle + 1 < args.cycles:
            time.sleep(args.interval)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
