"""Stale superseded metrics must not reappear on an active surface.

The supersession sweep was a one-time action. This is what keeps it true:
it fails if a superseded team-ratings-v1 value returns to a current report,
if a preserved artifact loses its warning, or if the replacement metadata
goes missing.

Historical references are permitted, but only where explicitly marked -
otherwise the certification artifacts themselves would trip the audit,
and an audit that cannot distinguish a record from a claim is useless.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
REPORTS = REPO / "apps" / "api" / "data" / "reports"
SUPERSEDED_DIR = REPORTS / "superseded"

# Values from the ORIGINAL team-ratings-v1 evaluation. Full precision, as
# they appeared, so a rounded reprint does not slip past.
SUPERSEDED_VALUES = [
    "0.215890590267", "0.621482778369", "7.39199904989", "10.1593060839",
    "1.35615512994", "-0.120180026015",
    "0.221923568383", "0.633493249615", "7.24884519863", "10.1623205683",
    "0.96086508827", "-0.112844304599",
]

WARNING = "SUPERSEDED — DO NOT CITE"

STATEMENT_FRAGMENT = (
    "simultaneous game results were previously applied in input-dependent order"
)

# Directories whose entire purpose is to record what was superseded.
HISTORICAL_REFERENCE_ROOTS = (
    REPO / "reports" / "integrity",
    SUPERSEDED_DIR,
)


def _is_historical_reference(path: Path) -> bool:
    return any(root in path.parents or root == path.parent for root in HISTORICAL_REFERENCE_ROOTS)


def _active_report_files() -> list[Path]:
    return [p for p in REPORTS.glob("*.*") if p.is_file()]


class TestNoStaleMetricOnAnActiveSurface:
    @pytest.mark.parametrize("value", SUPERSEDED_VALUES)
    def test_superseded_values_are_absent_from_active_reports(self, value: str) -> None:
        for p in _active_report_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            assert value not in text, f"{p.name} still contains superseded value {value}"

    def test_active_reports_carry_the_supersession_notice(self) -> None:
        """Permitted only WITH an adjacent warning - the sweep's rule."""
        for p in _active_report_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "team-ratings-v1" not in text:
                continue
            assert STATEMENT_FRAGMENT in text, f"{p.name} names the model without the notice"

    def test_the_notice_is_the_exact_agreed_statement(self) -> None:
        from fde_api.reports import SUPERSESSION_NOTICE

        expected = (
            "The original team-ratings-v1 historical evaluation is superseded because "
            "simultaneous game results were previously applied in input-dependent order. "
            "The corrected deterministic evaluation replaces those metrics. The change did "
            "not affect other model tiers, recommendation statuses, simulated wagers, or "
            "reported ROI."
        )
        assert expected == SUPERSESSION_NOTICE

    def test_the_notice_does_not_overclaim(self) -> None:
        """It must not read as though the whole engine was invalidated."""
        from fde_api.reports import SUPERSESSION_NOTICE

        assert "did not affect other model tiers" in SUPERSESSION_NOTICE
        for overclaim in ("all models", "entire engine", "all results", "invalidated"):
            assert overclaim not in SUPERSESSION_NOTICE.lower()


class TestPreservedArtifactsAreMarked:
    def test_the_superseded_directory_exists_and_is_marked(self) -> None:
        assert SUPERSEDED_DIR.is_dir()
        readme = (SUPERSEDED_DIR / "README.md").read_text(encoding="utf-8")
        assert WARNING in readme
        assert STATEMENT_FRAGMENT in readme

    def test_every_preserved_artifact_is_listed_in_the_manifest(self) -> None:
        manifest = json.loads((SUPERSEDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
        listed = {a["preserved_as"].split("/")[-1] for a in manifest["artifacts"]}
        on_disk = {p.name for p in SUPERSEDED_DIR.glob("SUPERSEDED_*")}
        assert on_disk == listed, f"unlisted: {on_disk - listed}, missing: {listed - on_disk}"

    def test_the_manifest_carries_the_required_provenance(self) -> None:
        manifest = json.loads((SUPERSEDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["warning"] == WARNING
        for field in ("supersession_reason", "superseding_commit", "superseding_tag",
                      "supersession_timestamp", "model_version", "test_seasons", "scope"):
            assert manifest[field], f"manifest missing {field}"
        for a in manifest["artifacts"]:
            for field in ("original_filename", "original_sha256", "original_generating_commit",
                          "preserved_as", "replacement_filename", "replacement_sha256"):
                assert a[field], f"artifact entry missing {field}"

    def test_original_and_replacement_are_distinguishable(self) -> None:
        manifest = json.loads((SUPERSEDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
        for a in manifest["artifacts"]:
            assert a["original_sha256"] != a["replacement_sha256"], a["original_filename"]

    def test_the_replacement_hash_matches_the_current_file(self) -> None:
        """A stale replacement hash would let the index point at something
        that no longer exists in that form."""
        import hashlib

        manifest = json.loads((SUPERSEDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
        for a in manifest["artifacts"]:
            current = REPORTS / a["replacement_filename"]
            actual = hashlib.sha256(current.read_bytes()).hexdigest()
            assert actual == a["replacement_sha256"], (
                f"{a['replacement_filename']} changed since the manifest was written"
            )

    def test_the_manifest_scopes_the_supersession(self) -> None:
        """It must say what is NOT superseded, or readers will assume all."""
        manifest = json.loads((SUPERSEDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
        scope = manifest["scope"].lower()
        assert "unchanged" in scope
        assert "roi" in scope


class TestReportIndexPointsAtCurrentArtifacts:
    def test_the_current_report_is_not_a_superseded_copy(self) -> None:
        for p in _active_report_files():
            assert not p.name.startswith("SUPERSEDED_"), p.name

    def test_the_current_report_records_its_own_provenance(self) -> None:
        report = json.loads((REPORTS / "phase2_reports.json").read_text(encoding="utf-8"))
        for field in ("generated_at", "code_commit", "dependency_lock_hash"):
            assert report.get(field), f"replacement artifact missing {field}"

    def test_one_evaluation_row_per_model_and_scope(self) -> None:
        """The generator used to append every run's evaluation, listing a
        model twice per scope with different numbers and no way to tell
        which was current."""
        report = json.loads((REPORTS / "phase2_reports.json").read_text(encoding="utf-8"))
        for scope, rows in report["4_to_7_model_performance_by_scope"].items():
            names = [r["model"] for r in rows]
            assert len(names) == len(set(names)), f"{scope} lists a model more than once"


class TestHistoricalReferencesRemainPermitted:
    def test_certification_artifacts_may_contain_the_old_values(self) -> None:
        """They are the record OF the supersession. If this ever fails, the
        audit has become too blunt to be useful."""
        sup = REPO / "reports" / "integrity" / "SUPERSESSION.md"
        text = sup.read_text(encoding="utf-8")
        assert any(v in text for v in SUPERSEDED_VALUES)
        assert _is_historical_reference(sup)
        assert WARNING in text
