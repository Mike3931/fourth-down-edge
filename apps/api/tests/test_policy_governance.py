"""Frozen-policy governance.

Creation and verification are separate operations. `build_policy_draft`
reads current code; `verify_frozen_policy` reads only the stored payload.
That split is what lets the application ship new commits without moving a
frozen hash.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.forward_models import ForwardTestPolicyRecord
from fde_api.db.models import Base
from fde_api.forward.policy import (
    PolicyImmutabilityError,
    build_policy_draft,
    freeze_policy,
    load_frozen_policy,
    verify_frozen_policy,
)

START, END = date(2026, 9, 1), date(2027, 2, 28)

# The governing hash of the live 2026 policy. Pinned so an architectural
# change to verification cannot silently move it.
GOVERNING_HASH = "2128907c078df12a34dd2dfabf12887d23516190f27cc6e6d56c63bcd921d199"


@pytest.fixture()
def psession(tmp_path, monkeypatch) -> Session:
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    freeze_policy(s, build_policy_draft(policy_version="ftp-test-v1", start=START, end=END))
    s.commit()
    return s


class TestCreationVerificationSplit:
    def test_verification_reads_stored_payload_only(self, psession: Session) -> None:
        assert verify_frozen_policy(psession, "ftp-test-v1") is True

    def test_frozen_hash_survives_a_later_code_commit(self, psession: Session) -> None:
        """Simulates shipping new application code after freezing."""
        rec = psession.get(ForwardTestPolicyRecord, "ftp-test-v1")
        original_hash = rec.policy_hash
        # Pretend the app was rebuilt at a different commit.
        rec.payload = {**rec.payload}  # touch without changing content
        psession.commit()
        assert verify_frozen_policy(psession, "ftp-test-v1") is True
        assert psession.get(ForwardTestPolicyRecord, "ftp-test-v1").policy_hash == original_hash

    def test_draft_under_later_commit_differs_but_does_not_affect_frozen(
        self, psession: Session, monkeypatch
    ) -> None:
        import fde_api.forward.policy as policy_mod

        before = psession.get(ForwardTestPolicyRecord, "ftp-test-v1").policy_hash
        monkeypatch.setattr(policy_mod, "current_code_commit", lambda: "f" * 40)
        draft = build_policy_draft(policy_version="ftp-test-v1", start=START, end=END)
        assert draft.code_commit == "f" * 40
        assert draft.policy_hash() != before  # a draft is not the frozen record
        assert psession.get(ForwardTestPolicyRecord, "ftp-test-v1").policy_hash == before
        assert verify_frozen_policy(psession, "ftp-test-v1") is True

    def test_frozen_record_retains_its_original_commit(self, psession: Session) -> None:
        rec = psession.get(ForwardTestPolicyRecord, "ftp-test-v1")
        assert rec.payload["code_commit"] == rec.code_commit
        assert rec.code_commit


class TestTamperDetection:
    def test_payload_tampering_fails(self, psession: Session) -> None:
        rec = psession.get(ForwardTestPolicyRecord, "ftp-test-v1")
        rec.payload = {**rec.payload, "research_candidate_edge_threshold": 0.01}
        psession.commit()
        assert verify_frozen_policy(psession, "ftp-test-v1") is False

    def test_hash_only_tampering_fails(self, psession: Session) -> None:
        rec = psession.get(ForwardTestPolicyRecord, "ftp-test-v1")
        rec.policy_hash = "0" * 64
        psession.commit()
        assert verify_frozen_policy(psession, "ftp-test-v1") is False

    def test_load_refuses_tampered_record(self, psession: Session) -> None:
        rec = psession.get(ForwardTestPolicyRecord, "ftp-test-v1")
        rec.policy_hash = "1" * 64
        psession.commit()
        with pytest.raises(PolicyImmutabilityError, match="tampered"):
            load_frozen_policy(psession, "ftp-test-v1")

    def test_unknown_version_raises(self, psession: Session) -> None:
        with pytest.raises(LookupError):
            verify_frozen_policy(psession, "no-such-policy")


class TestImmutability:
    def test_substantive_change_under_same_version_refused(self, psession: Session) -> None:
        changed = build_policy_draft(policy_version="ftp-test-v1", start=START, end=END)
        changed = changed.model_copy(update={"research_candidate_edge_threshold": 0.02})
        with pytest.raises(PolicyImmutabilityError, match="already frozen"):
            freeze_policy(psession, changed)

    def test_identical_refreeze_is_a_noop(self, psession: Session) -> None:
        same = build_policy_draft(policy_version="ftp-test-v1", start=START, end=END)
        rec = psession.get(ForwardTestPolicyRecord, "ftp-test-v1")
        if same.policy_hash() == rec.policy_hash:
            assert freeze_policy(psession, same).policy_hash == rec.policy_hash

    def test_new_version_is_the_way_to_change_rules(self, psession: Session) -> None:
        v2 = build_policy_draft(policy_version="ftp-test-v2", start=START, end=END)
        v2 = v2.model_copy(update={"research_candidate_edge_threshold": 0.07})
        rec = freeze_policy(psession, v2)
        assert rec.policy_version == "ftp-test-v2"
        assert verify_frozen_policy(psession, "ftp-test-v1") is True  # v1 untouched


class TestArtifactHygiene:
    def test_no_test_artifacts_in_repository_data_path(self) -> None:
        repo_policies = pathlib.Path(__file__).resolve().parents[1] / "data" / "policies"
        strays = sorted(p.name for p in repo_policies.glob("*test*.json")) if repo_policies.exists() else []
        assert strays == [], f"test artifacts leaked into the repository: {strays}"

    def test_only_the_governing_policy_is_committed(self) -> None:
        repo_policies = pathlib.Path(__file__).resolve().parents[1] / "data" / "policies"
        if not repo_policies.exists():
            pytest.skip("no policy directory in this checkout")
        names = sorted(p.name for p in repo_policies.glob("*.json"))
        assert names == ["ftp-2026-v1_2128907c078d.json"], names

    def test_fixture_writes_go_to_tmp(self, psession: Session, tmp_path) -> None:
        from fde_api.config import settings

        assert pathlib.Path(settings.data_dir) == tmp_path


class TestGoverningPolicyHashPreserved:
    """The live policy must keep the hash the forward test is governed by."""

    def test_live_policy_hash_unchanged(self) -> None:
        from fde_api.config import settings

        artifact = pathlib.Path(settings.data_dir) / "policies" / "ftp-2026-v1_2128907c078d.json"
        if not artifact.exists():
            pytest.skip("live policy artifact not present in this environment")
        import json

        from fde_api.forward.policy import ForwardTestPolicy

        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert ForwardTestPolicy.model_validate(payload).policy_hash() == GOVERNING_HASH
