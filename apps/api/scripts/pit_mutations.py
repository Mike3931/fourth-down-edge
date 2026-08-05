"""Mutation-test the point-in-time guards.

A passing test suite proves nothing about a guard unless removing the guard
makes something fail. This disables each critical temporal guard in turn,
runs the suite, and records which tests died.

A mutation that NOTHING kills is the finding: the guard is unprotected, and
a future edit could delete it silently. That is reported as a failure of
this script, not as a curiosity.

Every mutation is applied to a copy in memory and written to the file only
for the duration of one pytest run; the original is restored in a `finally`
block, and the script verifies byte-identical restoration before exiting.

Run:  python scripts/pit_mutations.py [--quick]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
REPORT = ROOT.parents[1] / "reports" / "integrity" / "pit-mutation-report.json"


@dataclass
class Mutation:
    """One deliberately broken guard."""

    name: str
    description: str
    path: Path
    original: str
    replacement: str
    # Tests expected to notice. Kept narrow so the run is fast and so a
    # mutation killed only by an UNRELATED test is visible as such.
    target: str
    killed_by: list[str] = field(default_factory=list)
    survived: bool = False


def _mutations() -> list[Mutation]:
    fwd = SRC / "fde_api" / "forward"
    return [
        Mutation(
            name="quote_visibility_after_cutoff",
            description=(
                "consensus stops excluding quotes observed after the cutoff, so a "
                "snapshot could be built from quotes that did not exist yet"
            ),
            path=fwd / "consensus.py",
            original="if q.observed_at > as_of_at:",
            replacement="if False and q.observed_at > as_of_at:",
            target="tests/test_pit_chain_guards.py",
        ),
        Mutation(
            name="weather_visibility_after_cutoff",
            description=(
                "weather lookup stops filtering on observation time, so a forecast "
                "issued after the cutoff becomes visible to it"
            ),
            path=fwd / "weather.py",
            original="WeatherForecastVintage.observed_at <= as_of_at,",
            replacement="WeatherForecastVintage.observed_at <= as_of_at + timedelta(days=365),",
            target="tests/test_pit_chain_guards.py",
        ),
        Mutation(
            name="injury_visibility_after_cutoff",
            description=(
                "injury lookup stops filtering on observation time, so a Sunday "
                "report becomes visible to a Tuesday prediction"
            ),
            path=fwd / "injuries.py",
            original="InjuryObservation.observed_at <= as_of_at,",
            replacement="InjuryObservation.observed_at <= as_of_at + timedelta(days=365),",
            target="tests/test_pit_chain_guards.py",
        ),
        Mutation(
            name="closing_snapshot_after_kickoff",
            description=(
                "the closing rule stops requiring the snapshot to precede kickoff, "
                "so a post-kickoff quote could be recorded as the close"
            ),
            path=fwd / "closing.py",
            original="ConsensusSnapshot.observed_at <= kickoff_utc,",
            replacement="ConsensusSnapshot.observed_at <= kickoff_utc + timedelta(days=1),",
            target="tests/test_execution_and_clv.py",
        ),
        Mutation(
            name="result_before_kickoff_accepted",
            description=(
                "result ingestion stops refusing a result observed before kickoff, "
                "so a game could be final before it started"
            ),
            path=fwd / "results.py",
            original="if result.observed_at < prev.kickoff_utc:",
            replacement="if False and result.observed_at < prev.kickoff_utc:",
            target="tests/test_pit_chain_guards.py",
        ),
        Mutation(
            name="result_visible_to_its_own_prediction",
            description=(
                "the result lookup stops applying the as-of filter, so a prediction "
                "could read the score of the game it is predicting"
            ),
            path=fwd / "results.py",
            original="stmt = stmt.where(ScheduleObservation.observed_at <= as_of)",
            replacement="stmt = stmt",
            target="tests/test_pit_chain_guards.py",
        ),
        Mutation(
            name="later_price_enters_earlier_evaluation",
            description=(
                "price selection stops filtering on observation time, so an "
                "evaluation could use a price observed after its cutoff"
            ),
            path=fwd / "prices.py",
            original="stmt = stmt.where(ManualBookPriceEntry.observed_at <= as_of)",
            replacement="stmt = stmt",
            target="tests/test_pit_chain_guards.py",
        ),
        # All three services share ONE tie-break rule, so there is one
        # mutation for it rather than three near-identical ones. The earlier
        # per-service mutations all survived: each inline copy was
        # unobservable because SQLite returns rows in id order and Python's
        # sort is stable, so dropping the id changed nothing any test could
        # see. Extracting the rule is what made it testable.
        Mutation(
            name="simultaneous_ordering_becomes_arbitrary",
            description=(
                "the shared newest() tie-break drops the identity, so two records "
                "sharing an instant resolve by input order instead of "
                "deterministically - stable on SQLite by accident, arbitrary on "
                "PostgreSQL"
            ),
            path=fwd / "ordering.py",
            original="return max(rows, key=lambda r: (when(r), ident(r)))",
            replacement="return max(rows, key=lambda r: when(r))",
            target="tests/test_pit_chain_guards.py",
        ),
        Mutation(
            name="ordered_listing_becomes_arbitrary",
            description=(
                "the shared ordering helper drops the identity tie-break, so a "
                "listing of records sharing an instant is not reproducible"
            ),
            path=fwd / "ordering.py",
            original="return sorted(rows, key=lambda r: (when(r), ident(r)))",
            replacement="return sorted(rows, key=lambda r: when(r))",
            target="tests/test_pit_chain_guards.py",
        ),
    ]


def _run(target: str) -> tuple[bool, list[str]]:
    """Run one target. Returns (passed, failing test ids)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-p", "no:cacheprovider",
         "-q", "--no-header", "-W", "ignore", "--tb=no", "-rf"],
        cwd=ROOT, capture_output=True, text=True,
    )
    failing = [
        ln.split(" ")[1] for ln in proc.stdout.splitlines()
        if ln.startswith("FAILED ")
    ]
    return proc.returncode == 0, failing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="skip the clean baseline run")
    args = parser.parse_args()

    mutations = _mutations()

    if not args.quick:
        print("baseline: the suite must be green before any mutation means anything")
        for target in sorted({m.target for m in mutations}):
            ok, failing = _run(target)
            if not ok:
                print(f"  BASELINE FAILED on {target}: {failing}")
                return 2
            print(f"  ok  {target}")

    results: list[dict] = []
    survivors: list[str] = []

    for m in mutations:
        source = m.path.read_text(encoding="utf-8")
        if m.original not in source:
            print(f"SKIP {m.name}: anchor not found in {m.path.name}")
            results.append({"mutation": m.name, "status": "ANCHOR_MISSING",
                            "path": m.path.name})
            survivors.append(m.name)
            continue
        mutated = source.replace(m.original, m.replacement, 1)
        try:
            m.path.write_text(mutated, encoding="utf-8")
            ok, failing = _run(m.target)
            m.killed_by = failing
            m.survived = ok
        finally:
            # Restore unconditionally, then check. The check does NOT return
            # from inside the finally: a return there swallows whatever
            # exception brought us here, which is the one case where knowing
            # what went wrong matters most.
            m.path.write_text(source, encoding="utf-8")
            restore_failed = m.path.read_text(encoding="utf-8") != source

        if restore_failed:
            print(f"FATAL: {m.path} was not restored to its original contents")
            return 3

        status = "SURVIVED" if m.survived else "KILLED"
        print(f"{status:9} {m.name}  ({len(m.killed_by)} test(s))")
        for t in m.killed_by[:4]:
            print(f"            killed by {t}")
        if m.survived:
            survivors.append(m.name)
        results.append({
            "mutation": m.name,
            "description": m.description,
            "file": m.path.name,
            "status": status,
            "killed_by": m.killed_by,
        })

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps({
            "note": (
                "Each entry is a deliberately disabled point-in-time guard. "
                "KILLED means at least one test failed with the guard removed, so "
                "the guard is load-bearing. SURVIVED means nothing noticed, which "
                "is a finding: the guard could be deleted silently."
            ),
            "total": len(results),
            "killed": sum(1 for r in results if r["status"] == "KILLED"),
            "survived": sum(1 for r in results if r["status"] != "KILLED"),
            "mutations": results,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"\nwrote {REPORT}")

    if survivors:
        print(f"\n{len(survivors)} MUTATION(S) SURVIVED: {survivors}")
        print("A guard nothing notices is a guard that can be deleted silently.")
        return 1
    print(f"\nall {len(results)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
