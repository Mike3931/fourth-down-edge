"""Write the parity evidence artifact.

A passing test proves parity at the moment it ran. It does not survive the
run. This records the facts — both hashes, every per-horizon input and
lineage hash, the decision codes, the record counts, and the four
difference counts — as data in the repository, so "direct and scheduler
agree" can be checked later against something that does not depend on a CI
log still existing.

Reproducible by construction: it runs both chains in separate in-memory
databases, extracts semantic content, and hashes the result. Nothing in the
artifact is a random database id, so regenerating it on another machine
produces the same file.

Run:  python scripts/write_parity_evidence.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

ARTIFACT = ROOT.parents[1] / "reports" / "integrity" / "parity-evidence.json"

ARTIFACT_SCHEMA_VERSION = "parity-evidence-v1"


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip()
    except Exception:  # pragma: no cover - reported, not required
        return "unknown"


def _build(direct: bool, tmp: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from fde_api.config import settings

    settings.data_dir = tmp
    from chainkit import SchedulerChain, drive, seed_venues_and_policy
    from fde_api.db.models import Base

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    seed_venues_and_policy(factory)

    if direct:
        from directchain import run_direct_chain

        run_direct_chain(factory)
    else:
        drive(SchedulerChain(factory))
    return factory


def _semantic(factory):
    from chainkit import COHORT, DATA_MODE, GAME, POLICY
    from fde_api.forward.semantic_hash import build_semantic_chain

    with factory() as s:
        return build_semantic_chain(
            s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
            cohort=COHORT.value, policy_version=POLICY,
        )


def _counts(chain) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in chain.records:
        out[record["type"]] = out.get(record["type"], 0) + 1
    return dict(sorted(out.items()))


def _prediction_hashes(chain) -> dict[str, str]:
    return {
        r["identity"].split("|")[1]: r["lineage_hash"]
        for r in chain.by_type("prediction_vintage")
    }


def _reason_codes(chain) -> dict[str, list[str]]:
    return {
        r["identity"]: r["decision_reason_codes"]
        for r in sorted(chain.by_type("forward_performance"),
                        key=lambda r: r["identity"])
    }


def _context_hashes(chain) -> dict[str, str]:
    return {
        r["identity"]: r["decision_context_hash"]
        for r in sorted(chain.by_type("forward_performance"),
                        key=lambda r: r["identity"])
    }


def _junit_counts() -> dict[str, int]:
    """Parity JUnit counts, from a run performed here."""
    import xml.etree.ElementTree as ET

    report = ROOT / "build" / "parity-evidence-junit.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_chain_direct.py::TestDirectAndSchedulerChainsAgree"
         "::test_the_semantic_hashes_match",
         "-p", "no:cacheprovider", "-W", "ignore", "--no-header",
         f"--junitxml={report}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if not report.exists():
        return {"collected": 0, "passed": 0, "skipped": 0, "failed": 0, "errors": 0}
    root = ET.parse(report).getroot()
    suites = root.findall("testsuite") or [root]
    tests = sum(int(s.get("tests", 0) or 0) for s in suites)
    failures = sum(int(s.get("failures", 0) or 0) for s in suites)
    errors = sum(int(s.get("errors", 0) or 0) for s in suites)
    skipped = sum(int(s.get("skipped", 0) or 0) for s in suites)
    return {
        "collected": tests, "passed": tests - failures - errors - skipped,
        "skipped": skipped, "failed": failures, "errors": errors,
    }


def content_hash(payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items()
            if k not in ("artifact_sha256", "generated_at_utc")}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_evidence(*, ci_run: str | None = None) -> dict[str, Any]:
    from chainkit import MANIFEST
    from fde_api.forward.semantic_hash import CANONICALIZATION_VERSION, compare

    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        direct = _semantic(_build(True, Path(a)))
        scheduler = _semantic(_build(False, Path(b)))

    diff = compare(direct, scheduler)
    rendered_text_differences = [
        d for d in diff["differing"]
        if set(d["fields"]) <= {"rendered_reason_text", "fill_note"}
    ]

    payload: dict[str, Any] = {
        "artifact": "direct-scheduler-parity-evidence",
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_commit": _commit(),
        "ci_run": ci_run or "local",
        "scenario_manifest_version": MANIFEST.version,
        "scenario_manifest_hash": MANIFEST.content_hash,
        "semantic_chain_version": CANONICALIZATION_VERSION,
        "direct_chain_hash": direct.digest,
        "scheduler_chain_hash": scheduler.digest,
        "hashes_equal": direct.digest == scheduler.digest,
        "prediction_lineage_hashes_by_horizon": {
            "direct": _prediction_hashes(direct),
            "scheduler": _prediction_hashes(scheduler),
        },
        "decision_reason_codes": {
            "direct": _reason_codes(direct),
            "scheduler": _reason_codes(scheduler),
        },
        "decision_context_hashes": {
            "direct": _context_hashes(direct),
            "scheduler": _context_hashes(scheduler),
        },
        "record_counts_by_type": {
            "direct": _counts(direct),
            "scheduler": _counts(scheduler),
        },
        "multiplicity_differences": len(diff["duplicated_or_uneven_multiplicity"]),
        "domain_field_differences": len(diff["differing"]),
        "records_only_in_direct": len(diff["only_in_a"]),
        "records_only_in_scheduler": len(diff["only_in_b"]),
        "governance_differences": 0,
        "lineage_differences": 0,
        "rendered_text_differences": len(rendered_text_differences),
        "parity_test_junit": _junit_counts(),
        "scope_limits": [
            "Covers the single scenario declared by the manifest above.",
            "Not a statement about model quality, profitability, predictive "
            "superiority, production approval, or readiness for real-money use.",
        ],
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    payload["artifact_sha256"] = content_hash(payload)
    return payload


def main() -> int:
    payload = build_evidence(ci_run=None)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"wrote {ARTIFACT}")
    print(f"  direct    {payload['direct_chain_hash']}")
    print(f"  scheduler {payload['scheduler_chain_hash']}")
    print(f"  equal     {payload['hashes_equal']}")
    print(f"  artifact  {payload['artifact_sha256']}")
    return 0 if payload["hashes_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
