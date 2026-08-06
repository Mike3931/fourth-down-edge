"""The twelve semantic race cells, named and validated.

The matrix is four entities by three race families. Twelve cells, each a
named test at the PUBLIC SERVICE boundary — not against the generic upsert
helper, because a helper that works says nothing about whether the service
calls it correctly.

Infrastructure tests in the same module (backend PIDs, dialect assertions,
no-raw-IntegrityError sweeps) are real and are reported separately. They
are not substitutes for a semantic cell, and counting them as one is how a
matrix comes to look complete while a family is missing.

Validated on the JUnit RESULT, not the exit code: a run whose selected
tests were all skipped exits 0.

Run:  python scripts/verify_race_matrix.py
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tests" / "test_pg_identity_races.py"
JUNIT = ROOT / "build" / "race-matrix-junit.xml"
MANIFEST = ROOT.parents[1] / "reports" / "integrity" / "race-matrix.json"

# entity -> family -> (class, test, service entry point, content varied,
#                      expected outcomes, expected rows, review expected)
MATRIX: dict[str, dict[str, dict[str, Any]]] = {
    "consensus_snapshot": {
        "exact_retry": {
            "test": "TestConsensusRaces::test_exact_retry_race_yields_one_row",
            "service": "fde_api.forward.consensus.build_consensus",
            "logical_identity": "game|market|cutoff|cohort|method_version",
            "content_varied": "none - identical quotes and window",
            "expected_outcomes": ["CREATED (at most one)", "EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": False,
        },
        "conflicting_payload": {
            "test": "TestConsensusConflictRace::test_conflicting_content_at_one_slot",
            "service": "fde_api.forward.consensus.build_consensus",
            "logical_identity": "game|market|cutoff|cohort|method_version",
            "content_varied": "max_age_minutes window, changing the eligible "
                              "quote set and therefore the median",
            "expected_outcomes": ["CREATED (at most one)", "CONFLICT or EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": True,
        },
        "distinct_version": {
            "test": "TestConsensusRaces::test_distinct_cutoffs_both_survive",
            "service": "fde_api.forward.consensus.build_consensus",
            "logical_identity": "game|market|cutoff|cohort|method_version",
            "content_varied": "different cutoff - a different slot",
            "expected_outcomes": ["CREATED", "CREATED"],
            "expected_rows": 2,
            "manual_review_expected": False,
        },
    },
    "research_evaluation": {
        "exact_retry": {
            "test": "TestEvaluationRaces::test_exact_retry_race_yields_one_row",
            "service": "fde_api.forward.ledger.record_evaluation_result",
            "logical_identity": "game|prediction|price|type|cohort|policy|model|context",
            "content_varied": "none",
            "expected_outcomes": ["CREATED (at most one)", "EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": False,
        },
        "conflicting_payload": {
            "test": "TestEvaluationRaces::test_conflicting_content_at_one_slot",
            "service": "fde_api.forward.ledger.record_evaluation_result",
            "logical_identity": "game|prediction|price|type|cohort|policy|model|context",
            "content_varied": "model probability, changing the analytical output",
            "expected_outcomes": ["CREATED (at most one)", "CONFLICT or EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": True,
        },
        "distinct_version": {
            "test": "TestEvaluationRaces::test_distinct_identities_both_survive",
            "service": "fde_api.forward.ledger.record_evaluation_result",
            "logical_identity": "game|prediction|price|type|cohort|policy|model|context",
            "content_varied": "evaluation type - the shape a remediated "
                              "decision context takes",
            "expected_outcomes": ["CREATED", "CREATED"],
            "expected_rows": 2,
            "manual_review_expected": False,
        },
    },
    "availability_assessment": {
        "exact_retry": {
            "test": "TestAvailabilityRaces::test_exact_retry_race_yields_one_row",
            "service": "fde_api.forward.injuries.assess_player_result",
            "logical_identity": "game|player|cutoff|cohort|method_version",
            "content_varied": "none",
            "expected_outcomes": ["CREATED (at most one)", "EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": False,
        },
        "conflicting_payload": {
            "test": "TestAvailabilityConflictRace::test_conflicting_content_at_one_slot",
            "service": "fde_api.forward.injuries.assess_player_result",
            "logical_identity": "game|player|cutoff|cohort|method_version",
            "content_varied": "game designation on the underlying observation, "
                              "changing the derived availability state",
            "expected_outcomes": ["CREATED (at most one)", "CONFLICT or EXISTING_IDENTICAL"],
            "expected_rows": 1,
            # Documented domain policy: a later cutoff supersedes, so a
            # human is not required. The outcome is still CONFLICT.
            "manual_review_expected": False,
        },
        "distinct_version": {
            "test": "TestAvailabilityRaces::test_distinct_cutoffs_both_survive",
            "service": "fde_api.forward.injuries.assess_player_result",
            "logical_identity": "game|player|cutoff|cohort|method_version",
            "content_varied": "different cutoff",
            "expected_outcomes": ["CREATED", "CREATED"],
            "expected_rows": 2,
            "manual_review_expected": False,
        },
    },
    "price_observation": {
        "exact_retry": {
            "test": "TestPriceObservationRaces::test_exact_retry_race_yields_one_row",
            "service": "fde_api.forward.prices.record_price_observation_result",
            "logical_identity": "game|market|selection|observed_at|cohort|mode|source",
            "content_varied": "none",
            "expected_outcomes": ["CREATED (at most one)", "EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": False,
        },
        "conflicting_payload": {
            "test": "TestPriceObservationRaces::test_conflicting_payload_race_keeps_one_row",
            "service": "fde_api.forward.prices.record_price_observation_result",
            "logical_identity": "game|market|selection|observed_at|cohort|mode|source",
            "content_varied": "american odds",
            "expected_outcomes": ["CREATED (at most one)", "CONFLICT or EXISTING_IDENTICAL"],
            "expected_rows": 1,
            "manual_review_expected": True,
        },
        "distinct_version": {
            "test": "TestPriceObservationRaces::test_distinct_observations_both_survive",
            "service": "fde_api.forward.prices.record_price_observation_result",
            "logical_identity": "game|market|selection|observed_at|cohort|mode|source",
            "content_varied": "different observation instant",
            "expected_outcomes": ["CREATED", "CREATED"],
            "expected_rows": 2,
            "manual_review_expected": False,
        },
    },
}

FAMILIES = ("exact_retry", "conflicting_payload", "distinct_version")


def required_tests() -> list[str]:
    return [
        MATRIX[entity][family]["test"]
        for entity in sorted(MATRIX)
        for family in FAMILIES
    ]


def _module_test_names() -> set[str]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    names.add(f"{node.name}::{item.name}")
    return names


def _silenced() -> list[str]:
    """Skip, skipif, xfail or a runtime skip anywhere in the module."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            for dec in node.decorator_list:
                rendered = ast.unparse(dec)
                marker = rendered.split("(")[0].split(".")[-1]
                if marker in {"skip", "skipif", "xfail"}:
                    out.append(f"{node.name}: @{rendered[:60]}")
        if isinstance(node, ast.Call):
            fn = ast.unparse(node.func)
            if fn in {"pytest.skip", "pytest.xfail"}:
                out.append(f"line {node.lineno}: {fn}()")
    return out


def main() -> int:
    problems: list[str] = []

    present = _module_test_names()
    missing = [t for t in required_tests() if t not in present]
    if missing:
        problems.append(f"matrix cells with no test: {missing}")

    silenced = _silenced()
    if silenced:
        problems.append(f"silencing markers in the race module: {silenced}")

    JUNIT.parent.mkdir(parents=True, exist_ok=True)
    if JUNIT.exists():
        JUNIT.unlink()
    node_ids = [f"tests/test_pg_identity_races.py::{t}" for t in required_tests()]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *node_ids, "-p", "no:cacheprovider",
         "-W", "ignore", "--no-header", f"--junitxml={JUNIT}", "-v"],
        cwd=ROOT, capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout[-6000:])

    counts = {"collected": 0, "passed": 0, "skipped": 0, "failed": 0, "errors": 0}
    if JUNIT.exists():
        root = ET.parse(JUNIT).getroot()
        suites = root.findall("testsuite") or [root]
        counts["collected"] = sum(int(s.get("tests", 0) or 0) for s in suites)
        counts["failed"] = sum(int(s.get("failures", 0) or 0) for s in suites)
        counts["errors"] = sum(int(s.get("errors", 0) or 0) for s in suites)
        counts["skipped"] = sum(int(s.get("skipped", 0) or 0) for s in suites)
        counts["passed"] = (counts["collected"] - counts["failed"]
                            - counts["errors"] - counts["skipped"])
    else:
        problems.append("no JUnit report was produced; the matrix did not run")

    if counts["collected"] != 12:
        problems.append(f"collected {counts['collected']} semantic cells, expected 12")
    if counts["passed"] != 12:
        problems.append(f"passed {counts['passed']}, expected 12")
    for key in ("skipped", "failed", "errors"):
        if counts[key]:
            problems.append(f"{counts[key]} {key}")

    manifest = {
        "artifact": "postgresql-domain-identity-race-matrix",
        "artifact_schema_version": "race-matrix-v1",
        "required_semantic_cells": 12,
        "families": list(FAMILIES),
        "matrix": MATRIX,
        "junit": counts,
        "supporting_infrastructure_tests_reported_separately": [
            "TestTheBackendIsReallyPostgres::test_dialect_is_postgresql",
            "TestTheBackendIsReallyPostgres::test_workers_hold_independent_connections",
            "TestTheBackendIsReallyPostgres::test_this_module_builds_no_sqlite_engine",
            "TestNoRawIntegrityErrorEscapes (3 tests)",
        ],
        "note": (
            "Infrastructure tests are real and are NOT substitutes for a "
            "semantic cell. Counting them as one is how a matrix looks "
            "complete while a family is missing."
        ),
        "problems": problems,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8", newline="\n")

    print("\n=== RACE MATRIX ===")
    print("    required cells  12")
    print(f"    collected       {counts['collected']}")
    print(f"    passed          {counts['passed']}")
    print(f"    skipped         {counts['skipped']}")
    print(f"    failed          {counts['failed']}")
    print(f"    errors          {counts['errors']}")
    print(f"    manifest        {MANIFEST}")
    if problems:
        print("\nRACE MATRIX FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nRACE MATRIX PASSED: 12 of 12 semantic cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
