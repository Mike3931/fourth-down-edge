"""The concurrency evidence artifact must stay true and tamper-evident.

A CI link is not durable evidence: logs expire, runs are deletable, and a
URL proves only that someone once had a URL. The artifact records the facts
themselves. These tests keep it honest — the hash is recomputed from the
data, so editing a recorded fact without regenerating the artifact fails,
and so does regenerating it without the facts changing.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = ROOT / "reports" / "integrity" / "pg-recovery-concurrency-evidence.json"
GENERATOR = Path(__file__).resolve().parents[1] / "scripts" / "write_pg_evidence.py"


def _generator():
    spec = importlib.util.spec_from_file_location("write_pg_evidence", GENERATOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def artifact() -> dict:
    assert ARTIFACT.exists(), f"{ARTIFACT} is missing; run scripts/write_pg_evidence.py"
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


class TestTheArtifactIsInternallyConsistent:
    def test_the_hash_matches_its_contents(self, artifact: dict) -> None:
        """The tamper check. Any edit to a recorded fact changes this."""
        mod = _generator()
        assert artifact["evidence_sha256"] == mod.evidence_hash(artifact)

    def test_the_generator_reproduces_the_file(self, artifact: dict) -> None:
        """Regenerating must be a no-op, or the file and the generator have
        drifted and one of them is lying."""
        mod = _generator()
        assert mod.build() == artifact

    def test_the_hash_is_computed_over_data_not_bytes(self) -> None:
        """Raw-byte hashing of a written file is what made an earlier
        governance artifact hash differently on Windows and Linux. The
        canonical form must be insensitive to line endings and key order."""
        mod = _generator()
        a = mod.evidence_hash({"z": 1, "a": 2, "evidence_sha256": "ignored"})
        b = mod.evidence_hash({"a": 2, "z": 1})
        assert a == b


class TestTheRecordedFactsAreTheOnesRequired:
    @pytest.mark.parametrize(
        "path,expected",
        [
            (("ci", "run_id"), "30914640864"),
            (("ci", "head_sha"), "2e95f6e6f23b460cd2718fe291fe932a59cd54db"),
            (("ci", "job_result"), "success"),
            (("ci", "step_result"), "success"),
            (("backend", "isolation_level"), "read committed"),
            (("backend", "sqlalchemy"), "2.0.51"),
            (("backend", "psycopg"), "3.2.13"),
            (("tests", "total"), 11),
            (("tests", "passed"), 11),
            (("tests", "failed"), 0),
            (("results", "initial_run_race"), "PASSED"),
            (("results", "recovery_race"), "PASSED"),
            (("results", "administrative_recovery_race"), "PASSED"),
            (("results", "mixed_initial_versus_recovery_race"), "PASSED"),
        ],
    )
    def test_a_required_fact_is_present_and_correct(
        self, artifact: dict, path: tuple[str, ...], expected: object
    ) -> None:
        node: object = artifact
        for key in path:
            assert isinstance(node, dict) and key in node, f"missing {'.'.join(path)}"
            node = node[key]
        assert node == expected

    def test_the_server_version_is_recorded(self, artifact: dict) -> None:
        assert artifact["backend"]["server_version"].startswith("16.14")

    def test_the_connection_count_is_recorded(self, artifact: dict) -> None:
        ex = artifact["execution"]
        assert ex["racing_workers"] >= 2, "a race needs at least two workers"
        assert ex["connection_pool_size"] >= ex["racing_workers"], (
            "a pool smaller than the worker count would serialise the race into a queue"
        )
        assert ex["independent_connections_asserted"] is True

    def test_the_synchronisation_method_is_recorded(self, artifact: dict) -> None:
        assert "Barrier" in artifact["execution"]["race_synchronisation"]

    def test_the_group_counts_sum_to_the_total(self, artifact: dict) -> None:
        groups = artifact["tests"]["groups"]
        assert sum(g["count"] for g in groups) == artifact["tests"]["total"]
        assert all(g["result"] == "passed" for g in groups)


class TestTheArtifactDoesNotOverclaim:
    def test_the_ci_url_is_marked_supplementary(self, artifact: dict) -> None:
        """The whole point: the URL is a convenience, not the evidence."""
        assert artifact["ci"]["url_is_supplementary"] is True

    def test_scope_limits_are_stated(self, artifact: dict) -> None:
        limits = " ".join(artifact["scope_limits"]).lower()
        assert "not a general statement about concurrency" in limits
        assert "profitability" in limits
        assert "real-money" in limits

    def test_no_claim_of_profitability_or_approval_appears(self, artifact: dict) -> None:
        # `claim` and `scope_limits` are where the DISCLAIMERS live, so they
        # name these phrases in order to deny them. Scanning them would make
        # the audit fire on its own safeguard - the same false positive that
        # made two earlier repository audits match their own docstrings.
        body = {k: v for k, v in artifact.items() if k not in ("claim", "scope_limits")}
        blob = json.dumps(body).lower()
        for phrase in (
            "production ready", "production-ready", "approved for production",
            "profitable", "predictive superiority", "real-money ready",
        ):
            assert phrase not in blob, f"artifact claims {phrase!r}"
