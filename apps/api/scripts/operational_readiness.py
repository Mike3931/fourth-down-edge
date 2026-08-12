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
    next_kickoff = session.scalar(
        select(func.min(ScheduleObservation.kickoff_utc)).where(
            ScheduleObservation.kickoff_utc >= at)
    )
    # Two very different reasons for "no game within 12h", and only one of
    # them is something to go and fix:
    #
    #   the slate does not REACH this instant  -> a coverage gap. Either
    #       nothing is ingested at all, or ingestion stops short of the
    #       date being asked about.
    #   the slate covers it and nothing is on  -> a Tuesday. Normal, and
    #       not a reason to go looking for a broken ingest.
    #
    # Both used to emit one blocker ending "A game that was never ingested
    # cannot be evaluated", which is a non-sequitur in the second case and
    # sends the reader hunting for a failure that has not happened.
    #
    # A target BEFORE the earliest kickoff is not a gap - the season simply
    # has not reached it, and there is a next kickoff to name. A target
    # AFTER the last one is: ingestion stops short of the date being asked
    # about, and no amount of waiting produces a game.
    covers_target = bool(hi and at <= hi + window)
    return {
        "distinct_games": games or 0,
        "earliest_kickoff": lo,
        "latest_kickoff": hi,
        "season_types": types,
        "games_near_target": same_day,
        "next_kickoff_at_or_after_target": next_kickoff,
        "slate_covers_target": covers_target,
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


def _budget(session) -> dict[str, Any]:
    """What the plan buys against what the cadence costs.

    Both numbers already exist; nothing compared them. A plan is not "small"
    or "large" in the abstract - it is either enough for the cadence the
    scheduler is configured to run, or it is not, and finding that out in
    week 3 is finding it out too late.
    """
    from fde_api.forward.quota import (
        budget_report,
        observed_plan_credits,
        plan_capacity,
    )

    plan = observed_plan_credits(session)
    budget = budget_report()
    required = budget["monthly_requirement_credits"]
    out: dict[str, Any] = {
        "observed_plan_credits": plan,
        "monthly_requirement_credits": required,
        "regular_season_credits": budget["regular_season_credits"],
        "minimum_viable_plan_credits": budget["minimum_viable_plan_credits"],
        "capacity": plan_capacity(plan),
    }
    if plan:
        out["shortfall_multiple"] = round(required / plan, 1) if plan else None
        out["sufficient_for_configured_cadence"] = plan >= required
    else:
        out["note"] = (
            "The plan is unknown until the provider answers once. Until then "
            "the configured absolutes apply, which assume a "
            f"{budget['configured']['monthly_plan_credits']}-credit plan."
        )
    return out


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
    budget = _budget(session)

    blockers: list[str] = []
    notes: list[str] = []
    if not slate["slate_covers_target"]:
        blockers.append(
            f"SLATE DOES NOT REACH {_fmt(at)}. It holds "
            f"{slate['distinct_games']} game(s), types {slate['season_types']}, "
            f"{_fmt(slate['earliest_kickoff'])} to {_fmt(slate['latest_kickoff'])}. "
            "A game that was never ingested cannot be evaluated."
        )
    elif not slate["target_in_slate"]:
        # Not a blocker. The slate reaches this instant and nothing happens
        # to be on, which is most days of the week.
        notes.append(
            f"No game kicks off within 12h of {_fmt(at)}. The slate covers "
            f"this date; the next kickoff at or after it is "
            f"{_fmt(slate['next_kickoff_at_or_after_target'])}. Nothing to fix."
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

    if budget.get("sufficient_for_configured_cadence") is False:
        blockers.append(
            f"PROVIDER PLAN TOO SMALL for the configured cadence: "
            f"{budget['observed_plan_credits']} credit(s) against a "
            f"{budget['monthly_requirement_credits']}/month requirement "
            f"({budget['shortfall_multiple']}x short). The scheduler will shed "
            "distant-game cadence and protect closes, but the intended research "
            "cadence cannot run. This is a subscription decision, not a bug."
        )

    # The verdict reads the capability list, not the blocker COUNT.
    #
    # Counting made severity a tally: three unrelated blockers outranked
    # two, so "no game kicks off in the next twelve hours" weighed the same
    # as "no provider key". It printed `VERDICT: BLOCKED` directly above
    # `CAN RUN NOW: + schedule, weather and injury capture`, which is the
    # report contradicting itself two lines apart.
    #
    # `capable` always contains the health and chain audit, which needs
    # nothing at all. BLOCKED therefore means that entry is the only one
    # left; anything beyond it is real work and makes the state PARTIAL.
    real_work = [c for c in capable if "reconciliation" not in c]
    if not blockers:
        verdict = VERDICT_READY
    elif real_work:
        verdict = VERDICT_PARTIAL
    else:
        verdict = VERDICT_BLOCKED

    return {
        "artifact": "operational-readiness",
        # v2: the verdict is derived from remaining capability rather than
        # from a blocker count, and a quiet day is a note rather than a
        # blocker. Same shape, different meaning, so the version moves.
        "artifact_schema_version": "operational-readiness-v2",
        "assessed_at_utc": datetime.now(UTC).isoformat(),
        "target_instant_utc": at.isoformat(),
        "verdict": verdict,
        "blockers": blockers,
        "notes": notes,
        "can_run_now": capable,
        "slate": {**slate,
                  "earliest_kickoff": _fmt(slate["earliest_kickoff"]),
                  "latest_kickoff": _fmt(slate["latest_kickoff"])},
        "policy": policy,
        "provider": provider,
        "budget": budget,
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
    if report["notes"]:
        # Reported, and separated from BLOCKING, because "nothing is on
        # tonight" is worth knowing and is not something to go and fix.
        print("  WORTH KNOWING:")
        for n in report["notes"]:
            # ASCII, like the "-" and "+" markers above. A cmd.exe console
            # on its default code page mangles anything else.
            print(f"    ~ {n}\n")
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
    b = report["budget"]
    cap = b.get("capacity", {})
    # "plan unknown credit(s)" is not a sentence. An unmeasured plan and a
    # measured one need different words, not the same template with a
    # placeholder dropped into the number slot.
    plan_credits = b["observed_plan_credits"]
    plan_text = (
        f"plan {plan_credits} credit(s)" if plan_credits
        else "plan size not yet recorded (no provider response)"
    )
    print(f"  budget   : {plan_text}; "
          f"cadence needs {b['monthly_requirement_credits']}/month, "
          f"season {b['regular_season_credits']}")
    if cap.get("plan_credits"):
        print(f"             = {cap['polls_per_window']} polls/window "
              f"(~{cap['polls_per_day']}/day), reserve {cap['reserve_credits']}")
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
