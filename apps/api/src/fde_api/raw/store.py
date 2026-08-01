"""Immutable raw-data layer.

Every ingestion run lands as a new, content-addressed version directory:

    raw/{provider}/{dataset}/{version_id}/
        {original filename}     <- source bytes exactly as received
        manifest.json           <- full provenance record

Rules enforced here, not by convention:
  * A version directory is never overwritten. A corrected source release
    (different payload hash) becomes a new version alongside the old one.
  * Re-ingesting an identical payload for the same (provider, dataset,
    params) is a no-op that returns the existing manifest — ingestion is
    idempotent without ever mutating history.
  * Failures are recorded too, so "no data" is distinguishable from
    "never tried".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from fde_api.util import current_code_commit, iso_now, sha256_bytes


class IngestionManifest(BaseModel):
    version_id: str
    provider: str
    dataset: str
    params: dict[str, Any]
    source_version: str | None
    source_updated_at: str | None
    observed_at: str
    ingested_at: str
    payload_sha256: str | None
    schema_version: str
    code_commit: str
    row_count: int | None
    status: str  # "success" | "failure"
    error: str | None = None
    artifact_filename: str | None = None
    deduplicated_of: str | None = None  # version_id this run matched byte-for-byte


class RawArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _dataset_dir(self, provider: str, dataset: str) -> Path:
        return self.root / provider / dataset

    def list_versions(self, provider: str, dataset: str) -> list[IngestionManifest]:
        ddir = self._dataset_dir(provider, dataset)
        if not ddir.exists():
            return []
        manifests = []
        for vdir in sorted(ddir.iterdir()):
            mpath = vdir / "manifest.json"
            if mpath.exists():
                manifests.append(IngestionManifest.model_validate_json(mpath.read_text(encoding="utf-8")))
        return manifests

    def latest_success(
        self, provider: str, dataset: str, params: dict[str, Any] | None = None
    ) -> IngestionManifest | None:
        candidates = [
            m
            for m in self.list_versions(provider, dataset)
            if m.status == "success" and (params is None or m.params == params)
        ]
        return candidates[-1] if candidates else None

    def artifact_path(self, manifest: IngestionManifest) -> Path:
        if manifest.artifact_filename is None:
            raise FileNotFoundError(f"Manifest {manifest.version_id} has no artifact")
        return self._dataset_dir(manifest.provider, manifest.dataset) / manifest.version_id / manifest.artifact_filename

    def write(
        self,
        *,
        provider: str,
        dataset: str,
        params: dict[str, Any],
        payload: bytes,
        filename: str,
        schema_version: str,
        observed_at: str,
        source_version: str | None = None,
        source_updated_at: str | None = None,
        row_count: int | None = None,
    ) -> IngestionManifest:
        payload_hash = sha256_bytes(payload)

        # Idempotence: identical bytes for identical params → keep history as-is.
        existing = self.latest_success(provider, dataset, params)
        if existing is not None and existing.payload_sha256 == payload_hash:
            return existing.model_copy(update={"deduplicated_of": existing.version_id})

        ingested_at = iso_now()
        version_id = f"{_ts_slug(ingested_at)}-{payload_hash[:8]}"
        vdir = self._dataset_dir(provider, dataset) / version_id
        if vdir.exists():
            raise FileExistsError(f"Refusing to overwrite immutable raw version {vdir}")
        vdir.mkdir(parents=True)

        (vdir / filename).write_bytes(payload)
        manifest = IngestionManifest(
            version_id=version_id,
            provider=provider,
            dataset=dataset,
            params=params,
            source_version=source_version,
            source_updated_at=source_updated_at,
            observed_at=observed_at,
            ingested_at=ingested_at,
            payload_sha256=payload_hash,
            schema_version=schema_version,
            code_commit=current_code_commit(),
            row_count=row_count,
            status="success",
            artifact_filename=filename,
        )
        (vdir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest

    def record_failure(
        self,
        *,
        provider: str,
        dataset: str,
        params: dict[str, Any],
        error: str,
        schema_version: str,
        observed_at: str,
    ) -> IngestionManifest:
        ingested_at = iso_now()
        version_id = f"{_ts_slug(ingested_at)}-failed"
        vdir = self._dataset_dir(provider, dataset) / version_id
        vdir.mkdir(parents=True, exist_ok=True)
        manifest = IngestionManifest(
            version_id=version_id,
            provider=provider,
            dataset=dataset,
            params=params,
            source_version=None,
            source_updated_at=None,
            observed_at=observed_at,
            ingested_at=ingested_at,
            payload_sha256=None,
            schema_version=schema_version,
            code_commit=current_code_commit(),
            row_count=None,
            status="failure",
            error=error,
        )
        (vdir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest


def _ts_slug(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y%m%dT%H%M%S%fZ")
