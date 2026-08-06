"""Every health check declares what KIND of health it reports.

Two different questions used to wear one word. Conflating them made the
scheduler suppress every candidate because 273 games were observed where
272 were expected — a statement about ingestion coverage, not about whether
any particular game's inputs were sound. The direct chain, which never
consulted platform state, produced a real candidate from identical data.

The first fix was a two-entry exclusion list. That was worse in a quiet
way: every NEW check silently inherited the power to suppress an analysis
until somebody remembered to add it, and the edit that never happens is the
one that matters. Scope is now declared per check and required.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fde_api.forward.health import (
    CHECK_SCOPES,
    SUPPRESSING_SCOPES,
    HealthScope,
    UnclassifiedHealthCheck,
    evaluation_health_context,
    may_suppress,
    scope_of,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def _report(*checks: dict) -> dict:
    return {"checks": list(checks)}


def _failing(cid: str, *, suppresses: bool = True) -> dict:
    return {
        "id": cid, "status": "FAILED", "severity": "CRITICAL",
        "suppresses_candidates": suppresses,
        "explanation": f"{cid} is failing", "remediation": "fix it",
    }


class TestEveryCheckIsClassified:
    def test_an_unclassified_check_is_refused(self) -> None:
        """Refused, not defaulted. A default is how a platform check
        acquires the power to suppress an analysis without anyone
        deciding that it should."""
        with pytest.raises(UnclassifiedHealthCheck, match="declares no scope"):
            scope_of("a_check_nobody_registered")

    def test_the_refusal_names_the_available_scopes(self) -> None:
        with pytest.raises(UnclassifiedHealthCheck) as e:
            scope_of("another_unregistered_check")
        for scope in HealthScope:
            assert scope.value in str(e.value)

    @pytest.mark.parametrize("check_id", sorted(CHECK_SCOPES))
    def test_each_registered_check_resolves(self, check_id: str) -> None:
        assert isinstance(scope_of(check_id), HealthScope)

    def test_lineage_checks_are_governance_by_prefix(self) -> None:
        """Generated per violation type rather than declared individually,
        so they are matched by prefix - but still classified."""
        assert scope_of("lineage_recovery_branch") is HealthScope.GOVERNANCE_INTEGRITY
        assert may_suppress("lineage_recovery_branch")

    def test_every_check_the_service_emits_is_classified(
        self, tmp_path, monkeypatch
    ) -> None:
        """The audit that matters: run the real checks and classify each.

        A check added to the service but not to the table fails here rather
        than silently gaining suppression power in production.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from chainkit import DATA_MODE, MANIFEST, seed_venues_and_policy
        from fde_api.config import settings
        from fde_api.db.models import Base
        from fde_api.forward.cohort import ProviderMode
        from fde_api.forward.health import run_health_checks

        # Redirect FIRST. `seed_venues_and_policy` freezes a policy to disk,
        # and without this it writes into the repository - which the
        # artifact-hygiene guard catches, as it did for this test.
        monkeypatch.setattr(settings, "data_dir", tmp_path)
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, future=True)
        seed_venues_and_policy(factory)
        with factory() as s:
            report = run_health_checks(
                s, data_mode=DATA_MODE, provider_mode=ProviderMode.FIXTURE,
                policy_version=MANIFEST.policy_version,
                now=MANIFEST.price_evaluation_slot,
            )
        unclassified: list[str] = []
        for check in report["checks"]:
            try:
                scope_of(check["id"])
            except UnclassifiedHealthCheck:
                unclassified.append(check["id"])
        assert not unclassified, f"health checks with no declared scope: {unclassified}"


class TestOnlyTheRightScopesSuppress:
    def test_decision_input_may_suppress(self) -> None:
        assert HealthScope.DECISION_INPUT in SUPPRESSING_SCOPES
        assert may_suppress("consensus_availability")

    def test_governance_may_suppress(self) -> None:
        assert HealthScope.GOVERNANCE_INTEGRITY in SUPPRESSING_SCOPES
        assert may_suppress("policy_integrity")

    def test_operational_platform_may_not_suppress(self) -> None:
        assert HealthScope.OPERATIONAL_PLATFORM not in SUPPRESSING_SCOPES
        assert not may_suppress("schedule_game_count")
        assert not may_suppress("odds_key_configured")
        assert not may_suppress("scheduler_heartbeat")

    def test_postgame_may_not_suppress_a_pregame_decision(self) -> None:
        """A missing close makes REPORTING incomplete. It cannot reach back
        and change a candidate that was correct when it was made."""
        assert HealthScope.POSTGAME_EVALUATION not in SUPPRESSING_SCOPES
        assert not may_suppress("missing_close")
        assert not may_suppress("missing_clv")


class TestTheScopeFilterDrivesSuppression:
    def test_a_decision_input_failure_suppresses(self) -> None:
        ctx = evaluation_health_context(_report(_failing("consensus_availability")))
        assert ctx.suppressed
        assert "consensus_availability" in ctx.failing_check_ids

    def test_a_slate_wide_count_does_not_suppress(self) -> None:
        """The exact failure that broke parity: 273 games where 272 were
        expected said nothing about the game being evaluated."""
        ctx = evaluation_health_context(_report(_failing("schedule_game_count")))
        assert not ctx.suppressed
        assert "schedule_game_count" in ctx.operational_check_ids

    def test_fixture_provider_mode_alone_does_not_suppress(self) -> None:
        ctx = evaluation_health_context(_report(_failing("odds_key_configured")))
        assert not ctx.suppressed

    def test_an_operational_failure_beside_a_decision_one_still_suppresses(
        self,
    ) -> None:
        """The operational check is reported, not ignored - it just does not
        drive the decision."""
        ctx = evaluation_health_context(_report(
            _failing("schedule_game_count"),
            _failing("consensus_availability"),
        ))
        assert ctx.suppressed
        assert ctx.failing_check_ids == ("consensus_availability",)
        assert ctx.operational_check_ids == ("schedule_game_count",)

    def test_a_postgame_failure_does_not_suppress(self) -> None:
        ctx = evaluation_health_context(_report(_failing("missing_close")))
        assert not ctx.suppressed

    def test_a_passing_check_never_suppresses(self) -> None:
        ok = {"id": "consensus_availability", "status": "OK",
              "suppresses_candidates": True, "explanation": "", "remediation": ""}
        assert not evaluation_health_context(_report(ok)).suppressed

    def test_check_ids_are_carried_not_explanations(self) -> None:
        """The context hash must not depend on prose - identifiers are
        stable across environments and sentences are not."""
        ctx = evaluation_health_context(_report(_failing("consensus_availability")))
        assert ctx.failing_check_ids == ("consensus_availability",)
        assert any("consensus_availability" in r for r in ctx.rendered_reasons)


class TestAddingChecksCannotSilentlyChangeSemantics:
    def test_a_new_operational_failure_does_not_change_suppression(self) -> None:
        before = evaluation_health_context(_report(_failing("consensus_availability")))
        after = evaluation_health_context(_report(
            _failing("consensus_availability"),
            _failing("scheduler_heartbeat"),
        ))
        assert before.suppressed == after.suppressed
        assert before.failing_check_ids == after.failing_check_ids

    def test_a_new_decision_input_failure_does_change_the_context(self) -> None:
        before = evaluation_health_context(_report(_failing("consensus_availability")))
        after = evaluation_health_context(_report(
            _failing("consensus_availability"),
            _failing("prediction_vintage_coverage"),
        ))
        assert before.failing_check_ids != after.failing_check_ids


class TestNoDecisionIsReconstructedFromProse:
    """§2: no production code may parse rendered text to infer semantics.

    The bridge that did this mapped four literal phrases to codes. It worked
    only while nobody reworded an explanation, and it made a decision depend
    on its own prose.
    """

    def test_the_phrase_matching_table_is_gone(self) -> None:
        src = (SRC / "fde_api" / "forward" / "decision_codes.py").read_text(
            encoding="utf-8")
        assert "_PHRASE_CODES" not in src
        assert "for phrase, code in" not in src

    def test_no_forward_module_matches_a_reason_phrase(self) -> None:
        """Parsed: a membership test whose left side is a string literal and
        whose right side is a reasons collection is the shape of the bridge.
        Docstrings that discuss it are documentation."""
        offenders: list[str] = []
        forward = SRC / "fde_api" / "forward"
        for path in sorted(forward.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                if not any(isinstance(op, ast.In) for op in node.ops):
                    continue
                if not (isinstance(node.left, ast.Constant)
                        and isinstance(node.left.value, str)):
                    continue
                rendered = ast.unparse(node)
                # A code-vocabulary membership test is fine; a prose one is
                # not. Codes are UPPER_SNAKE constants from ReasonCode.
                literal = node.left.value
                if literal.isupper() or ("_" in literal and literal.replace(
                        "_", "").isalnum() and literal.isupper()):
                    continue
                if "reason" in rendered.lower() and " " in literal:
                    offenders.append(f"{path.name}:{node.lineno}: {rendered[:80]}")
        assert not offenders, (
            "decision semantics reconstructed from prose:\n" + "\n".join(offenders)
        )

    def test_codes_are_validated_against_the_vocabulary(self) -> None:
        from fde_api.forward.decision_codes import UnknownReasonCode, normalise_codes

        with pytest.raises(UnknownReasonCode, match="unknown decision reason code"):
            normalise_codes(["EDGE_ABOVE_THRESHOLD", "NOT_A_REAL_CODE"])

    def test_codes_are_deduplicated_and_ordered(self) -> None:
        from fde_api.forward.decision_codes import normalise_codes

        out = normalise_codes(
            ["NOT_EXECUTED", "EDGE_BELOW_THRESHOLD", "NOT_EXECUTED"])
        assert out == ["EDGE_BELOW_THRESHOLD", "NOT_EXECUTED"]
        assert out == normalise_codes(list(reversed(out)))
