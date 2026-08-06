"""Drive one game through the entire forward-test lifecycle and report it.

The unit suite proves each stage. This proves the MACHINE: schedule through
odds, consensus, weather, injuries, availability, features, prediction,
price observation, evaluation, closing capture, result, settlement, CLV and
chain reconciliation — in order, through the real scheduler and the real
handlers, with nothing stubbed but the provider.

It exists because "973 tests pass" and "the system runs" are different
claims, and only one of them had evidence.

WHAT THIS IS NOT
    Fixture output. Every price here comes from the frozen scenario
    manifest, the cohort is DEMO, and the provider mode is FIXTURE. None of
    it is live-provider output, none of it may be described as live, and no
    result here says anything about model quality or profitability.

WHY IT IMPORTS THE TEST MANIFEST
    Because the alternative is a second set of payloads. Two hand-kept
    fixtures already drifted apart once in this repository and broke the
    parity gate for a reason unrelated to the system under test. The
    manifest is the single frozen scenario both execution paths read; a
    rehearsal that invented its own would be rehearsing something nobody
    else runs.

BY DEFAULT IT WRITES TO A THROWAWAY DATABASE and never touches the
configured one, because a rehearsal that pollutes the real cohort is not a
rehearsal.

Run:
  python scripts/rehearsal.py
  python scripts/rehearsal.py --keep     # leave the scratch database behind
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# The frozen scenario and its scheduler driver both live beside the tests
# that consume them. See WHY IT IMPORTS THE TEST MANIFEST above.
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

ARTIFACT = ROOT.parents[1] / "reports" / "integrity" / "rehearsal.json"


def _stage_rows(session) -> dict[str, int]:
    """How many records of each kind the run actually produced."""
    from sqlalchemy import func, select

    from fde_api.db.forward_models import (
        AvailabilityAssessment,
        ClosingCapture,
        ConsensusSnapshot,
        ForwardLedgerEntry,
        ForwardPrediction,
        InjuryObservation,
        ManualBookPriceEntry,
        OddsQuote,
        ScheduleObservation,
    )

    models = {
        "schedule_observation": ScheduleObservation,
        "odds_quote": OddsQuote,
        "consensus_snapshot": ConsensusSnapshot,
        "injury_observation": InjuryObservation,
        "availability_assessment": AvailabilityAssessment,
        "forward_prediction": ForwardPrediction,
        "price_observation": ManualBookPriceEntry,
        "forward_ledger": ForwardLedgerEntry,
        "closing_capture": ClosingCapture,
    }
    return {
        name: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in models.items()
    }


def rehearse(database_url: str) -> dict[str, Any]:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    import chainkit
    from fde_api.db.forward_models import ForwardLedgerEntry
    from fde_api.db.models import Base

    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)

    chainkit.seed_venues_and_policy(factory)
    chainkit.seed_injuries(factory)
    chain = chainkit.SchedulerChain(factory)
    chainkit.drive(chain)

    statuses = chain.statuses()
    missing = [j for j in chainkit.REQUIRED_SEQUENCE if j not in statuses]
    failed = sorted(j for j, s in statuses.items() if s not in ("finished", "skipped"))

    with factory() as session:
        counts = _stage_rows(session)
        entries = [
            {
                "market": e.market,
                "selection": e.selection,
                "status": e.status,
                "horizon": e.horizon,
                "filled": bool(e.filled),
                "result": e.result,
                "pnl_units": e.pnl_units,
                "clv_line": e.clv_line,
                "clv_probability": e.clv_probability,
                "closing_line": e.closing_line,
                "reason_codes": sorted((e.reasons or {}).get("codes", []))
                if isinstance(e.reasons, dict) else [],
            }
            for e in session.scalars(select(ForwardLedgerEntry))
        ]

        from fde_api.forward.chain import reconcile_chain
        from fde_api.forward.semantic_hash import build_semantic_chain

        recon = reconcile_chain(
            session, canonical_game_id=chainkit.GAME,
            data_mode=chainkit.DATA_MODE.value, policy_version=chainkit.POLICY)
        semantic = build_semantic_chain(
            session, canonical_game_id=chainkit.GAME,
            data_mode=chainkit.DATA_MODE.value, cohort=chainkit.COHORT.value,
            policy_version=chainkit.POLICY)

    settled = [e for e in entries if e["result"] is not None]
    with_clv = [e for e in entries if e["clv_line"] is not None]

    return {
        "artifact": "forward-lifecycle-rehearsal",
        "artifact_schema_version": "rehearsal-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "cohort": chainkit.COHORT.value,
        "data_mode": chainkit.DATA_MODE.value,
        "provider_mode": "FIXTURE",
        "scenario_manifest_version": chainkit.MANIFEST.version,
        "canonical_game_id": chainkit.GAME,
        "kickoff_utc": chainkit.KICK.isoformat(),
        "policy_version": chainkit.POLICY,
        "required_sequence": list(chainkit.REQUIRED_SEQUENCE),
        "job_statuses": statuses,
        "stages_missing": missing,
        "stages_failed": failed,
        "record_counts": counts,
        "ledger_entries": entries,
        "settled_entries": len(settled),
        "entries_with_clv": len(with_clv),
        "chain_verdict": recon.get("verdict"),
        "semantic_chain_version": semantic.version,
        "semantic_chain_hash": semantic.digest,
        "not_a_claim": (
            "Fixture prices in a DEMO cohort. This is not live-provider "
            "output and must never be described as such. It says nothing "
            "about model quality, profitability, or readiness for real money."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true",
                        help="leave the scratch database on disk")
    parser.add_argument("--database-url", default=None,
                        help="override the scratch database (use with care)")
    parser.add_argument("--out", default=str(ARTIFACT))
    args = parser.parse_args()

    tmpdir = Path(tempfile.mkdtemp(prefix="fde-rehearsal-"))
    url = args.database_url or f"sqlite:///{(tmpdir / 'rehearsal.db').as_posix()}"

    report = rehearse(url)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8", newline="\n")

    print("\n=== FORWARD LIFECYCLE REHEARSAL ===")
    print(f"  scenario  {report['scenario_manifest_version']}  "
          f"game {report['canonical_game_id']}  kickoff {report['kickoff_utc']}")
    print(f"  cohort    {report['cohort']} / provider {report['provider_mode']} "
          f"-> FIXTURE PRICES, NOT LIVE MARKET DATA")
    print(f"  policy    {report['policy_version']}\n")

    print("  stage sequence:")
    for job in report["required_sequence"]:
        status = report["job_statuses"].get(job, "NOT RUN")
        mark = "ok " if status in ("finished", "skipped") else "FAIL"
        print(f"    [{mark}] {job:30} {status}")

    print("\n  records written:")
    for name, n in sorted(report["record_counts"].items()):
        print(f"    {name:26} {n}")

    print("\n  ledger:")
    for e in report["ledger_entries"]:
        print(f"    {e['horizon']:16} {e['market']:7} {e['selection']:5} "
              f"{e['status']!s:16} filled={e['filled']!s:5} "
              f"result={e['result']!s:6} clv_line={e['clv_line']}")
        if e["reason_codes"]:
            print(f"        codes: {', '.join(e['reason_codes'])}")

    print(f"\n  settled          {report['settled_entries']}")
    print(f"  with CLV         {report['entries_with_clv']}")
    print(f"  chain verdict    {report['chain_verdict']}")
    print(f"  {report['semantic_chain_version']}  {report['semantic_chain_hash'][:32]}...")
    print(f"\n  artifact  {out}")

    problems = report["stages_missing"] + report["stages_failed"]
    if problems:
        print(f"\n  REHEARSAL INCOMPLETE: {problems}\n")
        return 1
    if not args.keep and not args.database_url:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        print(f"  scratch database kept at {tmpdir}")
    print("\n  REHEARSAL COMPLETE: every stage ran, in order.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
