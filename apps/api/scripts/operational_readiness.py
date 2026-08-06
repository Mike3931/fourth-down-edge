"""Can the platform run against a real game right now, and if not, what is blocking?

Three questions get conflated when someone asks "can we test tonight":

  1. Is the GAME in the slate?          — a game that was never ingested
                                          cannot be evaluated, and nothing
                                          downstream will say so plainly.
  2. Is the POLICY window open?         — the forward test is governed by an
                                          immutable policy with a start and
                                          end date. Outside that window there
                                          is no cohort to record into.
  3. Can market data be CAPTURED?       — no provider key, no consensus, and
                                          therefore no price to evaluate.

Each has a different answer and a different fix, and answering only the
loudest one is how an evening gets spent fixing the wrong thing.

This reads only. It captures nothing, evaluates nothing, and writes no
record. It never prints the provider key or any part of it.

Run:  python scripts/operational_readiness.py [--at 2026-09-10T00:20:00Z]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

VERDICT_READY = "READY"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_BLOCKED = "BLOCKED"


def _fmt(dt: datetime | None) -> str:
    return dt.isoformat() if dt else "—"


def _slate(session, at: datetime) -> dict[str, Any]:
    """What the ingested slate covers, and whether `at` falls inside it."""
    from sqlalchemy import func, select

    from fde_api.db.forward_models import ScheduleObservation

    lo, hi = session.execute(
        select(func.min(ScheduleObservation.kickoff_utc),
               func.max(ScheduleObservation.kickoff_utc))
    ).first()
    types = sorted(
        r[0] for r in session.execute(
            select(ScheduleObservation.season_type).distinct()).all()
    )
    games = session.scalar(
        select(func.count(func.distinct(ScheduleObservation.canonical_game_id))))

    window = timedelta(hours=12)
    same_day = list(session.scalars(
        select(ScheduleObservation.canonical_game_id).where(
            ScheduleObservation.kickoff_utc >= at - window,
            ScheduleObservation.kickoff_utc <= at + window,
        ).distinct()
    ))
    return {
        "distinct_games": games or 0,
        "earliest_kickoff": lo,
        "latest_kickoff": hi,
        "season_types": types,
        "games_near_target": same_day,
        "target_in_slate": bool(same_day),
    }


def _policy(session, at: datetime) -> dict[str, Any]:
    from sqlalchemy import select

    from fde_api.db.forward_models import ForwardTestPolicyRecord

    rows = list(session.scalars(select(ForwardTestPolicyRecord)))
    in_force = []
    for p in rows:
        start = datetime.fromisoformat(f"{p.start_date}T00:00:00+00:00")
        end = datetime.fromisoformat(f"{p.end_date}T23:59:59+00:00")
        if start <= at <= end:
            in_force.append(p.policy_version)
    return {
        "policies": [
            {"version": p.policy_version, "start": p.start_date, "end": p.end_date,
             "model_version": p.model_version}
            for p in rows
        ],
        "in_force_at_target": in_force,
        "window_open": bool(in_force),
    }


def _provider() -> dict[str, Any]:
    """Whether a key is present. The VALUE is never read into the report."""
    key = os.environ.get("FDE_ODDS_API_KEY") or ""
    return {
        "env_var": "FDE_ODDS_API_KEY",
        "configured": bool(key.strip()),
        # Deliberately not the key, not a prefix, not a suffix, not a hash:
        # a hash of a short secret is a cracking target, and a prefix is a
        # head start. Presence is the only fact needed here.
        "note": "presence only; the value is never read, logged, or derived from",
    }


def _health(session, at: datetime) -> dict[str, Any]:
    from fde_api.forward.health import HealthScope, run_health_checks, scope_of
    from fde_api.forward.modes import DataMode

    report = run_health_checks(session, now=at, data_mode=DataMode.LIVE_RESEARCH)
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for check in report["checks"]:
        if check.get("status") == "OK":
            continue
        try:
            scope = scope_of(check["id"]).value
        except Exception:
            scope = HealthScope.GOVERNANCE_INTEGRITY.value
        by_scope.setdefault(scope, []).append({
            "id": check["id"],
            "severity": check.get("severity"),
            "explanation": check.get("explanation"),
            "remediation": check.get("remediation"),
        })
    return {"total_checks": len(report["checks"]), "not_ok_by_scope": by_scope}


def assess(session, at: datetime) -> dict[str, Any]:
    slate = _slate(session, at)
    policy = _policy(session, at)
    provider = _provider()
    health = _health(session, at)

    blockers: list[str] = []
    if not slate["target_in_slate"]:
        blockers.append(
            f"NO GAME IN SLATE within 12h of {_fmt(at)}. The slate holds "
            f"{slate['distinct_games']} game(s), types {slate['season_types']}, "
            f"{_fmt(slate['earliest_kickoff'])} to {_fmt(slate['latest_kickoff'])}. "
            "A game that was never ingested cannot be evaluated."
        )
    if not policy["window_open"]:
        windows = ", ".join(f"{p['version']} [{p['start']}..{p['end']}]"
                            for p in policy["policies"]) or "none frozen"
        blockers.append(
            f"NO POLICY IN FORCE at {_fmt(at)}. Known: {windows}. The forward "
            "test is governed by an immutable policy window; outside it there "
            "is no evaluation cohort to record into."
        )
    if not provider["configured"]:
        blockers.append(
            "FDE_ODDS_API_KEY IS NOT SET. No market data can be captured, so "
            "there is no consensus and no price to evaluate. Set it in the "
            "backend environment; it is never committed or logged."
        )

    # What can still run is worth stating, because "blocked" reads as
    # "nothing works" and that is usually false.
    capable: list[str] = []
    if provider["configured"]:
        capable.append("odds capture and consensus construction")
    if slate["distinct_games"]:
        capable.append("schedule, weather and injury capture for the ingested slate")
    capable.append("data-health reconciliation and the record-chain audit")
    if policy["window_open"] and provider["configured"] and slate["target_in_slate"]:
        capable.append("evaluation, ledger, closing capture and settlement")

    if not blockers:
        verdict = VERDICT_READY
    elif len(blockers) == 3:
        verdict = VERDICT_BLOCKED
    else:
        verdict = VERDICT_PARTIAL

    return {
        "artifact": "operational-readiness",
        "artifact_schema_version": "operational-readiness-v1",
        "assessed_at_utc": datetime.now(UTC).isoformat(),
        "target_instant_utc": at.isoformat(),
        "verdict": verdict,
        "blockers": blockers,
        "can_run_now": capable,
        "slate": {**slate,
                  "earliest_kickoff": _fmt(slate["earliest_kickoff"]),
                  "latest_kickoff": _fmt(slate["latest_kickoff"])},
        "policy": policy,
        "provider": provider,
        "health": health,
        "not_a_claim": (
            "Readiness is about plumbing and governance only. It says nothing "
            "about model quality, profitability, or suitability for real money."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", default=None,
                        help="target instant, ISO 8601 UTC (default: now)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    at = (datetime.fromisoformat(args.at.replace("Z", "+00:00"))
          if args.at else datetime.now(UTC))
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)

    from sqlalchemy.orm import sessionmaker

    from fde_api.db.engine import get_engine

    with sessionmaker(bind=get_engine(), future=True)() as session:
        report = assess(session, at)

    if args.json:
        import json
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0 if report["verdict"] == VERDICT_READY else 1

    print(f"\n=== OPERATIONAL READINESS at {report['target_instant_utc']} ===\n")
    print(f"  VERDICT: {report['verdict']}\n")
    if report["blockers"]:
        print("  BLOCKING:")
        for b in report["blockers"]:
            print(f"    - {b}\n")
    print("  CAN RUN NOW:")
    for c in report["can_run_now"]:
        print(f"    + {c}")
    print(f"\n  slate    : {report['slate']['distinct_games']} game(s), "
          f"types {report['slate']['season_types']}")
    print(f"             {report['slate']['earliest_kickoff']} .. "
          f"{report['slate']['latest_kickoff']}")
    print(f"  policy   : {[p['version'] for p in report['policy']['policies']]}, "
          f"in force at target: {report['policy']['in_force_at_target'] or 'none'}")
    print(f"  provider : {report['provider']['env_var']} "
          f"{'configured' if report['provider']['configured'] else 'NOT SET'}")
    fails = report["health"]["not_ok_by_scope"]
    print(f"  health   : {report['health']['total_checks']} checks, "
          f"{sum(len(v) for v in fails.values())} not OK")
    for scope, items in sorted(fails.items()):
        print(f"      {scope}")
        for i in items:
            print(f"        [{i['severity']}] {i['id']}: {i['explanation']}")
    print()
    return 0 if report["verdict"] == VERDICT_READY else 1


if __name__ == "__main__":
    sys.exit(main())
