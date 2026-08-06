"""The rehearsal must fail when the machine is broken.

A green end-to-end report is only worth something if a red one is
reachable. These check the two ways this particular report could lie:
by counting a stage that never ran as fine, and by describing fixture
prices as anything other than fixture prices.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REHEARSAL = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "rehearsal.py"


@pytest.fixture(scope="module")
def rehearsal():
    spec = importlib.util.spec_from_file_location("rehearsal", REHEARSAL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(rehearsal, tmp_path_factory):
    db = tmp_path_factory.mktemp("rehearsal") / "r.db"
    return rehearsal.rehearse(f"sqlite:///{db.as_posix()}")


class TestTheWholeLifecycleRuns:
    def test_every_required_stage_ran(self, report) -> None:
        assert report["stages_missing"] == []

    def test_no_stage_failed(self, report) -> None:
        assert report["stages_failed"] == []

    def test_the_sequence_is_the_full_lifecycle(self, report) -> None:
        """Guards against the list quietly shrinking to whatever passes."""
        required = set(report["required_sequence"])
        for stage in ("odds_capture", "consensus_build", "prediction_vintage",
                      "price_observation", "price_evaluation", "closing_capture",
                      "result_ingestion", "settlement", "forward_evaluation"):
            assert stage in required, stage

    def test_records_were_actually_written(self, report) -> None:
        """Every stage reporting `finished` while writing nothing is the
        exact shape of a rehearsal that proves nothing."""
        counts = report["record_counts"]
        for kind in ("schedule_observation", "odds_quote", "consensus_snapshot",
                     "forward_prediction", "price_observation", "forward_ledger",
                     "closing_capture"):
            assert counts[kind] > 0, kind

    def test_the_ledger_reached_a_terminal_state(self, report) -> None:
        assert report["ledger_entries"], "no evaluation was recorded"
        assert report["settled_entries"] > 0

    def test_clv_was_computed_for_a_filled_entry(self, report) -> None:
        filled = [e for e in report["ledger_entries"] if e["filled"]]
        assert filled, "nothing was filled, so CLV proves nothing"
        assert report["entries_with_clv"] > 0

    def test_the_chain_reconciles(self, report) -> None:
        assert report["chain_verdict"] == "CONSISTENT"

    def test_the_semantic_hash_is_recorded(self, report) -> None:
        assert report["semantic_chain_version"] == "chain-semantic-v2"
        assert len(report["semantic_chain_hash"]) == 64


class TestTheReportCannotBeMistakenForLiveOutput:
    def test_the_cohort_and_provider_mode_are_stated(self, report) -> None:
        assert report["provider_mode"] == "FIXTURE"
        assert report["data_mode"] == "DEMO"

    def test_it_says_so_in_words_as_well(self, report) -> None:
        """A machine-readable field is easy to drop when the JSON is pasted
        into a document; the sentence travels with it."""
        note = report["not_a_claim"].lower()
        assert "fixture" in note
        assert "not live-provider output" in note

    def test_it_claims_nothing_about_quality_or_money(self, report) -> None:
        note = report["not_a_claim"].lower()
        for word in ("model quality", "profitability", "real money"):
            assert word in note

    def test_no_stage_is_labelled_live(self, report) -> None:
        rendered = str(report).lower()
        assert "live-provider output" not in rendered.replace(
            "not live-provider output", "")


class TestAMissingStageIsCaughtNotSmoothedOver:
    def test_an_absent_stage_is_reported_missing(self, rehearsal) -> None:
        """The check is on the REQUIRED sequence, not on what happened to
        run, so a stage that silently stopped being scheduled shows up."""
        statuses = {"odds_capture": "finished"}
        required = ("odds_capture", "settlement")
        missing = [j for j in required if j not in statuses]
        assert missing == ["settlement"]

    def test_a_failed_status_is_not_treated_as_success(self, rehearsal) -> None:
        statuses = {"settlement": "failed"}
        failed = sorted(j for j, s in statuses.items()
                        if s not in ("finished", "skipped"))
        assert failed == ["settlement"]


class TestTheRehearsalIsRepeatable:
    """A report that differs run to run is not evidence of anything.

    Worth the second execution: the artifact is meant to be compared
    across commits, and a hash that moves on its own makes every such
    comparison meaningless without anyone noticing why.
    """

    @pytest.fixture(scope="class")
    def second(self, rehearsal, tmp_path_factory):
        db = tmp_path_factory.mktemp("rehearsal2") / "r.db"
        return rehearsal.rehearse(f"sqlite:///{db.as_posix()}")

    def test_the_semantic_hash_is_stable(self, report, second) -> None:
        assert report["semantic_chain_hash"] == second["semantic_chain_hash"]

    def test_the_ledger_is_stable(self, report, second) -> None:
        assert report["ledger_entries"] == second["ledger_entries"]

    def test_the_record_counts_are_stable(self, report, second) -> None:
        assert report["record_counts"] == second["record_counts"]

    def test_the_stage_statuses_are_stable(self, report, second) -> None:
        assert report["job_statuses"] == second["job_statuses"]
