"""Run the parity test and verify its JUnit result, not just the exit code.

A pytest run whose only selected test was SKIPPED exits 0. So "the suite
passed" does not mean parity was checked — it can equally mean parity was
quietly not run, which is the failure mode this gate exists to prevent.
That is not hypothetical: the parity test spent two increments skipped with
a truthful reason while every CI run reported success.

So this asserts on the JUnit XML: collected >= 1, passed >= 1, and zero
skipped, failed or errored. A skip is a failure here, by design.

Run:  python scripts/verify_parity_junit.py
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "build" / "parity-junit.xml"

# The named test that must pass. Fully qualified so a rename is a loud
# failure rather than a silent zero-collected run.
PARITY_TEST = (
    "tests/test_chain_direct.py::TestDirectAndSchedulerChainsAgree"
    "::test_the_semantic_hashes_match"
)


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if REPORT.exists():
        REPORT.unlink()

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", PARITY_TEST,
         "-p", "no:cacheprovider", "-W", "ignore", "--no-header",
         f"--junitxml={REPORT}", "-v"],
        cwd=ROOT, capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout[-4000:])
    sys.stderr.write(proc.stderr[-2000:])

    if not REPORT.exists():
        print("FAIL: no JUnit report was produced; the test did not run at all")
        return 2

    root = ET.parse(REPORT).getroot()
    suites = root.findall("testsuite") or [root]
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0) or 0)
    collected = totals["tests"]
    passed = collected - totals["failures"] - totals["errors"] - totals["skipped"]

    print("\n=== PARITY GATE ===")
    print(f"    test       {PARITY_TEST}")
    print(f"    collected  {collected}")
    print(f"    passed     {passed}")
    print(f"    skipped    {totals['skipped']}")
    print(f"    failed     {totals['failures']}")
    print(f"    errors     {totals['errors']}")

    problems: list[str] = []
    if collected < 1:
        problems.append("the parity test was not collected")
    if passed < 1:
        problems.append("the parity test did not pass")
    if totals["skipped"]:
        problems.append(
            f"{totals['skipped']} skipped - a skipped parity test is a failure "
            "here, because a skipped run exits 0 and looks like a pass"
        )
    if totals["failures"]:
        problems.append(f"{totals['failures']} failed")
    if totals["errors"]:
        problems.append(f"{totals['errors']} errored")

    if problems:
        print("\nPARITY GATE FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nPARITY GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
