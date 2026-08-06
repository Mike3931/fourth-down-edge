"""Run the forward-test scheduler.

Everything the scheduler does was already built and tested; what was
missing was any way to start it outside a test. `Scheduler(...)` and
`register_all(...)` appeared only in `tests/`, which is why the
`scheduler_running` health check reports "no scheduler runs recorded" —
truthfully. An engine with no ignition is not a working engine.

Three refusals are deliberate, because each is a way a run could quietly
record something untrue:

  * A CLOSED POLICY WINDOW stops the run. Outside the window there is no
    evaluation cohort, so anything captured would land in no forward test
    and mean nothing later.

  * FIXTURE data in a LIVE_RESEARCH cohort stops the run unless it is
    asked for by name. Fixture output must never be stored or described as
    live-provider output, and the easiest way for that to happen is a
    default nobody looked at.

  * A LIVE provider mode with no key stops the run rather than silently
    degrading, so that "no key" is never mistaken for "no prices".

The provider key is read from FDE_ODDS_API_KEY by the adapter itself.
This script never reads it, never prints it, and never accepts it as an
argument.

Run:
  python scripts/run_scheduler.py --once --dry-run          # what is due
  python scripts/run_scheduler.py --once                    # one pass
  python scripts/run_scheduler.py --interval 60             # keep running
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("fde.runner")


def _build(args: argparse.Namespace):
    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine
    from fde_api.forward.cohort import Cohort, ProviderMode
    from fde_api.forward.handlers import register_all
    from fde_api.forward.modes import DataMode
    from fde_api.forward.scheduler import Scheduler

    factory = sessionmaker(bind=get_engine(), future=True)
    scheduler = Scheduler(
        factory,
        cohort=Cohort(args.cohort),
        provider_mode=ProviderMode(args.provider_mode),
        data_mode=DataMode(args.data_mode),
        policy_version=args.policy_version,
    )
    register_all(scheduler)
    return scheduler, factory


def _policies_in_force(session, at: datetime) -> tuple[list[str], list[str]]:
    """(versions in force at `at`, all known windows as text).

    Deliberately NOT imported from the readiness script. A cross-script
    import depends on `scripts` resolving as a package, which it does under
    some pytest invocations and not others - it passed locally and failed
    in CI on exactly that difference. An operational script that refuses to
    start should not be able to fail because a REPORTING script moved.
    """
    from sqlalchemy import select

    from fde_api.db.forward_models import ForwardTestPolicyRecord

    in_force: list[str] = []
    windows: list[str] = []
    for p in session.scalars(select(ForwardTestPolicyRecord)):
        windows.append(f"{p.policy_version} [{p.start_date}..{p.end_date}]")
        start = datetime.fromisoformat(f"{p.start_date}T00:00:00+00:00")
        end = datetime.fromisoformat(f"{p.end_date}T23:59:59+00:00")
        if start <= at <= end:
            in_force.append(p.policy_version)
    return in_force, windows


def _preflight(args: argparse.Namespace, factory) -> list[str]:
    """Reasons this run must not proceed."""
    from fde_api.forward.cohort import ProviderMode

    problems: list[str] = []

    with factory() as session:
        in_force, known_windows = _policies_in_force(session, datetime.now(UTC))

    if not in_force and not args.allow_closed_window:
        windows = ", ".join(known_windows) or "none frozen"
        problems.append(
            f"no forward-test policy is in force right now ({windows}). "
            "Captured records would belong to no evaluation cohort. Pass "
            "--allow-closed-window only for a capture-only rehearsal that is "
            "not meant to produce forward-test evidence."
        )

    mode = ProviderMode(args.provider_mode)
    if mode is ProviderMode.LIVE and not (os.environ.get("FDE_ODDS_API_KEY") or "").strip():
        problems.append(
            "provider mode is LIVE but FDE_ODDS_API_KEY is not set. Refusing "
            "so that 'no key' is never mistaken for 'no prices'."
        )
    if mode is ProviderMode.FIXTURE and args.data_mode == "LIVE_RESEARCH" \
            and not args.allow_fixture_in_live_research:
        problems.append(
            "provider mode FIXTURE with data mode LIVE_RESEARCH would store "
            "fixture output inside the live-research cohort. Pass "
            "--allow-fixture-in-live-research to say that is what you mean, "
            "or use --data-mode DEMO."
        )
    return problems


def _summarise(results: list[dict[str, Any]]) -> dict[str, int]:
    """Count by `status`, which is the key run_job actually returns.

    Reading a key that is not there yields a tidy-looking tally of
    UNKNOWNs - a summary that is confidently wrong rather than absent.
    """
    counts: dict[str, int] = {}
    for r in results:
        key = str(r.get("status", "MISSING_STATUS"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="seconds between passes when not --once")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is due; run nothing")
    parser.add_argument("--cohort", default="burn_in",
                        choices=["fixture", "demo", "burn_in", "official_forward_test"])
    parser.add_argument("--provider-mode", default="FIXTURE",
                        choices=["FIXTURE", "SANDBOX", "LIVE"])
    parser.add_argument("--data-mode", default="DEMO", choices=["DEMO", "LIVE_RESEARCH"])
    parser.add_argument("--policy-version", default=None)
    parser.add_argument("--allow-closed-window", action="store_true")
    parser.add_argument("--allow-fixture-in-live-research", action="store_true")
    parser.add_argument("--max-passes", type=int, default=0,
                        help="stop after N passes (0 = unlimited)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )

    scheduler, factory = _build(args)

    print(f"\ncohort={args.cohort} provider_mode={args.provider_mode} "
          f"data_mode={args.data_mode} policy={args.policy_version or 'auto'}")

    # A dry run writes nothing, so it is not gated on the preflight. Being
    # unable to LOOK at what is due, because a real run would have been
    # refused, is the kind of strictness that gets worked around rather
    # than respected. The refusals are still reported, just not fatal.
    if args.dry_run:
        for p in _preflight(args, factory):
            print(f"  would refuse to run: {p}")
        due = scheduler.due_jobs()
        print(f"\n{len(due)} job(s) due:")
        for job, slot in due:
            print(f"  {slot.isoformat()}  {job.name}"
                  f"{'  [critical]' if job.critical else ''}")
        print("\ndry run: nothing was executed\n")
        return 0

    problems = _preflight(args, factory)
    if problems:
        print("\nREFUSING TO RUN:")
        for p in problems:
            print(f"  - {p}")
        print()
        return 2

    # Startup reconciliation first: it is what turns an interrupted previous
    # run into a recorded, explainable gap rather than a silent one.
    recon = scheduler.reconcile_startup()
    print(f"startup reconciliation: {json.dumps(recon, default=str)[:400]}")

    def _stop(signum, _frame) -> None:
        log.info("signal %s received; finishing the current job then stopping", signum)
        scheduler.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not on the main thread, or the platform lacks the signal.
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _stop)

    passes = 0
    total: dict[str, int] = {}
    try:
        while not scheduler.shutting_down:
            results = scheduler.tick()
            passes += 1
            counts = _summarise(results)
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            if results:
                log.info("pass %d: %d job(s) %s", passes, len(results), counts)
            if args.once or (args.max_passes and passes >= args.max_passes):
                break
            time.sleep(max(1.0, args.interval))
    finally:
        print(f"\n{passes} pass(es); outcomes {total or 'none'}")
        print(json.dumps(scheduler.health(), indent=2, default=str)[:1500])
        print()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    sys.exit(main())
