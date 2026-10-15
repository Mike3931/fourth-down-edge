"""Run rest-feature experiments in a NEW workspace from preserved public CSV bytes."""

import argparse
import hashlib
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-file", type=Path, required=True)
    parser.add_argument("--games-csv", type=Path, required=True)
    args = parser.parse_args()
    target = args.database_file.resolve()
    if target.exists() or target.name.lower() == "fde.db":
        parser.error("NEW isolated database required; existing studies are never adopted")
    from fde_api.forward.workspace import initialize_workspace

    initialize_workspace(target)
    os.environ["FDE_DATABASE_URL"] = "sqlite:///" + target.as_posix()
    from sqlalchemy.orm import Session

    from fde_api.backtest.feature_ablation import run_ablation
    from fde_api.canonical.load_games import load_games_csv
    from fde_api.config import settings
    from fde_api.db.engine import get_engine
    from fde_api.ingest import _mirror
    from fde_api.raw import RawArtifactStore
    from fde_api.util import utc_now

    settings.database_url = os.environ["FDE_DATABASE_URL"]
    settings.data_dir = target.with_suffix(".research-data")
    settings.raw_dir = settings.data_dir / "raw"
    settings.artifacts_dir = settings.data_dir / "artifacts"
    settings.reports_dir = settings.data_dir / "reports"
    settings.ensure_dirs()
    payload = args.games_csv.read_bytes()
    with Session(get_engine(settings.database_url)) as s:
        raw = RawArtifactStore(settings.raw_dir).write(
            provider="nflverse",
            dataset="games",
            params={},
            payload=payload,
            filename="games.csv",
            schema_version="games-v1",
            observed_at=utc_now().isoformat(),
        )
        _mirror(s, raw)
        load_games_csv(s, payload, seasons=list(range(2010, 2027)), source_manifest_version=raw.version_id)
        s.commit()
        result = run_ablation(
            s, seasons=[2022, 2023, 2024, 2025], source_hash=hashlib.sha256(payload).hexdigest()
        )
        s.commit()
        output = settings.reports_dir / (result["run_id"] + ".json")
        with output.open("x") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
        print(output)


if __name__ == "__main__":
    main()
