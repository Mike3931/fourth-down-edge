"""Immutable raw store: versioning, idempotence, failures, hashes."""

from __future__ import annotations

import pytest

from fde_api.raw import RawArtifactStore
from fde_api.util import iso_now


@pytest.fixture()
def store(tmp_path):
    return RawArtifactStore(tmp_path / "raw")


def _write(store: RawArtifactStore, payload: bytes, params=None):
    return store.write(
        provider="testprov", dataset="ds", params=params or {"season": 2025},
        payload=payload, filename="data.csv", schema_version="v1", observed_at=iso_now(),
    )


def test_hash_reproducibility(store: RawArtifactStore) -> None:
    m = _write(store, b"a,b\n1,2\n")
    import hashlib

    assert m.payload_sha256 == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert store.artifact_path(m).read_bytes() == b"a,b\n1,2\n"


def test_idempotent_reingestion_creates_no_new_version(store: RawArtifactStore) -> None:
    m1 = _write(store, b"same")
    m2 = _write(store, b"same")
    assert m2.deduplicated_of == m1.version_id
    assert len(store.list_versions("testprov", "ds")) == 1


def test_corrected_release_creates_new_version_never_overwrites(store: RawArtifactStore) -> None:
    m1 = _write(store, b"original")
    m2 = _write(store, b"corrected")
    versions = store.list_versions("testprov", "ds")
    assert len(versions) == 2
    assert store.artifact_path(m1).read_bytes() == b"original"  # history intact
    assert store.artifact_path(m2).read_bytes() == b"corrected"
    assert store.latest_success("testprov", "ds").version_id == m2.version_id


def test_failure_recorded_with_provenance(store: RawArtifactStore) -> None:
    m = store.record_failure(
        provider="testprov", dataset="ds", params={"season": 2025},
        error="HTTP 404", schema_version="v1", observed_at=iso_now(),
    )
    assert m.status == "failure" and m.error == "HTTP 404"
    assert m.payload_sha256 is None
    # failures never become latest_success
    assert store.latest_success("testprov", "ds") is None


def test_manifest_carries_required_provenance(store: RawArtifactStore) -> None:
    m = _write(store, b"x")
    for field in ("provider", "dataset", "params", "observed_at", "ingested_at",
                  "payload_sha256", "schema_version", "code_commit", "status"):
        assert getattr(m, field) is not None
