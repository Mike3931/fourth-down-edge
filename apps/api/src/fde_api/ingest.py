"""Ingestion orchestration: fetch → immutable raw artifact → DB mirror →
canonical load. Each step is recorded; failures land as failure manifests
and data-quality events instead of vanishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from fde_api.canonical import load_games_csv, load_pbp_team_stats, load_rosters_parquet
from fde_api.config import settings
from fde_api.db.models import IngestionRun
from fde_api.providers import NflverseProvider, ProviderAdapter
from fde_api.raw import IngestionManifest, RawArtifactStore


@dataclass
class IngestReport:
    manifests: list[IngestionManifest] = field(default_factory=list)
    loads: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _mirror(session: Session, m: IngestionManifest) -> None:
    session.merge(
        IngestionRun(
            version_id=m.version_id,
            provider=m.provider,
            dataset=m.dataset,
            params=m.params,
            payload_sha256=m.payload_sha256,
            schema_version=m.schema_version,
            code_commit=m.code_commit,
            row_count=m.row_count,
            status=m.status,
            error=m.error,
            source_version=m.source_version,
            source_updated_at=m.source_updated_at,
            observed_at=datetime.fromisoformat(m.observed_at),
            ingested_at=datetime.fromisoformat(m.ingested_at),
        )
    )


def ingest_dataset(
    session: Session,
    provider: ProviderAdapter,
    dataset: str,
    params: dict[str, Any],
    store: RawArtifactStore | None = None,
) -> IngestionManifest:
    store = store or RawArtifactStore(settings.raw_dir)
    try:
        fetched = provider.fetch(dataset, params)
    except Exception as e:
        manifest = store.record_failure(
            provider=provider.name,
            dataset=dataset,
            params=params,
            error=f"{type(e).__name__}: {e}",
            schema_version=provider.schema_version(dataset),
            observed_at=_now_iso(),
        )
        _mirror(session, manifest)
        raise
    manifest = store.write(
        provider=provider.name,
        dataset=dataset,
        params=params,
        payload=fetched.payload,
        filename=fetched.filename,
        schema_version=provider.schema_version(dataset),
        observed_at=fetched.observed_at,
        source_version=fetched.source_version,
        source_updated_at=fetched.source_updated_at,
    )
    _mirror(session, manifest)
    return manifest


def ingest_nflverse(session: Session, seasons: list[int] | None = None) -> IngestReport:
    """Full nflverse ingestion for the given seasons: games (one shared
    file), then per-season play-by-play and rosters."""
    seasons = seasons or list(settings.default_seasons)
    settings.ensure_dirs()
    store = RawArtifactStore(settings.raw_dir)
    provider = NflverseProvider()
    report = IngestReport()

    games_manifest = ingest_dataset(session, provider, "games", {"scope": "all"}, store)
    report.manifests.append(games_manifest)
    payload = store.artifact_path(games_manifest).read_bytes()
    games_res = load_games_csv(
        session, payload, seasons=seasons, source_manifest_version=games_manifest.version_id
    )
    report.loads.append(
        {
            "dataset": "games",
            "seasons": seasons,
            "games_loaded": games_res.games_loaded,
            "games_skipped": games_res.games_skipped,
            "odds_rows": games_res.odds_rows,
            "quality_events": games_res.quality_events,
        }
    )
    session.flush()

    for season in seasons:
        for dataset, loader in (("pbp", "pbp"), ("rosters", "rosters")):
            try:
                m = ingest_dataset(session, provider, dataset, {"season": season}, store)
                report.manifests.append(m)
                path = store.artifact_path(m)
                if loader == "pbp":
                    r = load_pbp_team_stats(session, path, season=season)
                    report.loads.append(
                        {
                            "dataset": f"pbp_{season}",
                            "team_games": r.team_games_loaded,
                            "plays": r.plays_scanned,
                        }
                    )
                else:
                    r2 = load_rosters_parquet(session, path, season=season)
                    report.loads.append(
                        {
                            "dataset": f"rosters_{season}",
                            "players": r2.players_loaded,
                            "entries": r2.entries_loaded,
                            "no_gsis": r2.rows_without_gsis,
                        }
                    )
                session.flush()
            except Exception as e:  # keep going; the report shows the hole
                report.errors.append(f"{dataset} {season}: {type(e).__name__}: {e}")

    return report


def _now_iso() -> str:
    from fde_api.util import iso_now

    return iso_now()
