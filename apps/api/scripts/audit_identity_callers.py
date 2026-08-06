"""No call site may discard an identity outcome.

`upsert_by_identity` returns three outcomes, and the whole reason for
returning three instead of a row is that CONFLICT means something the
caller has to know about. A caller that reads `.record` and never looks at
`.outcome` gets the right row and misses that two writers disagreed — which
is precisely the silence the typed outcome was introduced to end.

Two rules, both mechanical:

  1. A call to a `*_result` service may not be a bare expression statement.
     `record_evaluation_result(...)` on a line by itself throws the outcome
     away and reads, at a glance, as if it did not.

  2. A module that calls one must handle the outcomes: either through
     `handled()` / `dispatch()` (which make all three branches mandatory
     by construction) or by naming all three outcomes itself.

Tests are exempt from rule 2 and NOT from rule 1: a test that asserts on
one outcome is doing its job, but a test that drops the value entirely is
asserting nothing.

Run:  python scripts/audit_identity_callers.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
MANIFEST = ROOT.parents[1] / "reports" / "integrity" / "identity-caller-audit.json"

# The four services that return an IdentityResult.
RESULT_SERVICES = {
    "record_evaluation_result",
    "record_price_observation_result",
    "assess_player_result",
    "upsert_by_identity",
}
# build_consensus returns (snapshot, report) and folds the outcome into the
# report's typed reasons, so it is checked by name rather than by shape.
CONSENSUS_SERVICE = "build_consensus"

OUTCOME_NAMES = ("CREATED", "EXISTING_IDENTICAL", "CONFLICT")
TOTAL_HANDLERS = ("handled", "dispatch")


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _raises_lines(tree: ast.AST) -> set[int]:
    """Line numbers inside a `with pytest.raises(...)` block.

    A call there is expected to raise, so its return value does not exist
    to be discarded. Narrow on purpose: only `raises`, only as a with-item.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        items = [i.context_expr for i in node.items]
        if not any(isinstance(c, ast.Call) and _called_name(c) == "raises"
                   for c in items):
            continue
        for child in ast.walk(node):
            if hasattr(child, "lineno"):
                lines.add(child.lineno)
    return lines


def _discarded_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Calls whose return value goes nowhere."""
    expected_to_raise = _raises_lines(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        name = _called_name(node.value)
        if name in RESULT_SERVICES and node.lineno not in expected_to_raise:
            out.append((node.lineno, name))
    return out


def _handles_outcomes(source: str, tree: ast.AST) -> tuple[bool, str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) in TOTAL_HANDLERS:
            return True, f"total dispatch via {_called_name(node)}()"
    named = [n for n in OUTCOME_NAMES if n in source]
    if len(named) == len(OUTCOME_NAMES):
        return True, "names all three outcomes"
    return False, f"names only {named or 'none'} of the three outcomes"


def audit() -> dict[str, Any]:
    findings: list[str] = []
    callers: dict[str, Any] = {}

    for path in sorted([*SRC.rglob("*.py"), *TESTS.rglob("*.py")]):
        source = path.read_text(encoding="utf-8")
        if not any(s in source for s in (*RESULT_SERVICES, CONSENSUS_SERVICE)):
            continue
        tree = ast.parse(source)
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        is_test = rel.startswith("tests/")

        discarded = _discarded_calls(tree)
        for lineno, name in discarded:
            findings.append(
                f"{rel}:{lineno}: {name}(...) as a bare statement discards the "
                "identity outcome"
            )

        entry: dict[str, Any] = {"discarded": [f"{n}@{ln}" for ln, n in discarded]}
        # Rule 2 applies to the modules that DEFINE or invoke the services in
        # application code. A module that merely imports the name for typing
        # is not a caller.
        invokes = any(
            isinstance(n, ast.Call) and _called_name(n) in RESULT_SERVICES
            for n in ast.walk(tree)
        )
        if invokes and not is_test:
            ok, how = _handles_outcomes(source, tree)
            entry["handling"] = how
            if not ok:
                findings.append(f"{rel}: calls an identity service but {how}")
        callers[rel] = entry

    return {
        "artifact": "identity-caller-audit",
        "artifact_schema_version": "identity-caller-audit-v1",
        "services": sorted(RESULT_SERVICES | {CONSENSUS_SERVICE}),
        "total_dispatch_helpers": list(TOTAL_HANDLERS),
        "files_examined": len(callers),
        "callers": callers,
        "findings": findings,
        "note": (
            "handled()/dispatch() take all three branches as required keyword "
            "arguments, so omitting one is a TypeError at the call site rather "
            "than a silence at run time."
        ),
    }


def main() -> int:
    report = audit()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"examined {report['files_examined']} file(s); wrote {MANIFEST}")
    if report["findings"]:
        print("\nIDENTITY CALLER AUDIT FAILED:")
        for f in report["findings"]:
            print(f"  - {f}")
        return 1
    print("IDENTITY CALLER AUDIT PASSED: no discarded outcomes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
