"""Prior regular-season results first observed now, never historical forecasts."""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime

from sqlalchemy.orm import Session

from fde_api.db.research_models import ResearchSource
from fde_api.forward.intelligence import capture_schedule_results, digest, source_url
from fde_api.forward.schedule import ingest_schedule
from fde_api.ingest import _mirror
from fde_api.raw import RawArtifactStore

VERSION = "training-history-v1"
FIELDS = ("game_id", "season", "game_type", "week", "home_team", "away_team",
          "gameday", "gametime", "stadium_id", "stadium", "location", "home_score", "away_score")


def capture_training_history(s: Session, *, payload: bytes, season: int,
                             captured_at: datetime, store: RawArtifactStore) -> ResearchSource:
    """Reuse an already downloaded public schedule in an authorized prospective workspace.

    The semantic key excludes current-season games and market columns. Identical
    history is not normalized again every polling cycle. Revisions retain all
    prior evidence and use the existing conservative identity/result ingestors.
    """
    if captured_at.tzinfo is None:
        raise ValueError("Aware capture time required")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    required = {"game_id", "season", "game_type", "week", "home_team", "away_team",
                "gameday", "home_score", "away_score"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Training history changed schema")
    rows = [{key: row.get(key, "") for key in FIELDS} for row in reader
            if 2010 <= int(row["season"]) < season and row["game_type"] == "REG"]
    if not rows:
        raise ValueError("Prior regular-season training history unavailable")
    rows.sort(key=lambda r: (r["game_id"], digest(r)))
    semantic_hash = digest(rows)
    key = digest([VERSION, season, semantic_hash])
    prior = s.get(ResearchSource, key)
    if prior is not None:
        return prior
    raw = store.write(provider="nflverse", dataset="research_schedule_history",
                      params={"season": season}, payload=payload, filename="games.csv",
                      schema_version=VERSION, observed_at=captured_at.isoformat(),
                      source_updated_at=None, row_count=len(rows))
    _mirror(s, raw)
    selected = io.StringIO()
    writer = csv.DictWriter(selected, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    filtered = selected.getvalue().encode()
    years = sorted({int(row["season"]) for row in rows})
    outcomes = [ingest_schedule(s, filtered, season=year, observed_at=captured_at,
                               source_manifest_version=raw.version_id).as_dict() for year in years]
    coverage = {"normalization_version": VERSION, "semantic_hash": semantic_hash,
                "source_rows": len(rows), "seasons": years, "schedule_outcomes": outcomes,
                "publication_semantics": "First observed at capture; not archived historical point-in-time evidence",
                "quality": "SECONDARY_PUBLIC", "complete_availability": False,
                "limitations": ["REG only, 2010 onward; not a claim of complete season coverage",
                                "Historical results support future training, never backfilled prospective performance"]}
    coverage.update(capture_schedule_results(s, rows, key, captured_at))
    batch = ResearchSource(id=key, dataset="schedule_history", season=season, provider="nflverse",
                           payload_hash=hashlib.sha256(payload).hexdigest(),
                           source_url=source_url("schedule", season), published_at=None,
                           captured_at=captured_at, coverage=coverage)
    s.add(batch)
    s.flush()
    return batch
