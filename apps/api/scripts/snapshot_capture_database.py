"""Make a consistent, verified SQLite snapshot without modifying source rows.

Source is opened SQLite read-only; backup() captures a consistent logical
snapshot including committed WAL data. Never uploads, deletes, edits, migrates,
or deduplicates evidence. The destination must not exist.

Usage:
  python apps/api/scripts/snapshot_capture_database.py \
      apps/api/data/fde.db /secure-backups/fde-2026-10-12.db

IMPORTANT: Stop the scheduler and any writers before using this for an
upgrade decision; a live backup is consistent at a moment, but subsequent
commits will exist outside its snapshot. Both file and sidecar metadata are
recorded in an adjacent manifest. Keep both files private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source: Path, destination: Path) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().absolute()
    if not source.is_file():
        raise ValueError('Source must be a regular SQLite file')
    if source == destination or destination.exists():
        raise FileExistsError('Destination must be a NEW file, not the source')
    if not destination.parent.is_dir():
        raise FileNotFoundError(f'Destination directory does not exist: {destination.parent}')
    manifest = Path(str(destination) + '.manifest.json')
    if manifest.exists():
        raise FileExistsError(f'Manifest exists: {manifest}')

    sidecars = [Path(str(source) + suffix) for suffix in ('-wal', '-shm')]
    source_files = [source] + [p for p in sidecars if p.exists()]
    source_metadata_before = {
        p.name: {'size_bytes': p.stat().st_size, 'sha256': _sha256(p)}
        for p in source_files
    }

    # O_EXCL prevents a second caller racing the existence check and
    # accidentally overwriting a previous snapshot.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    success = False
    manifest_created = False
    try:
        with closing(sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True)) as src:
            src.execute('PRAGMA query_only=ON')
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=256, sleep=0.05)
                # Produce a self-contained snapshot; hash only after both
                # connections close and all committed WAL pages are folded in.
                dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                dst.execute('PRAGMA journal_mode=DELETE')
                result = dst.execute('PRAGMA integrity_check').fetchone()
                if result is None or result[0] != 'ok':
                    raise sqlite3.DatabaseError(f'Backup failed integrity_check: {result}')
                has_alembic = dst.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                ).fetchone() is not None
                versions = ([x[0] for x in dst.execute(
                    "SELECT version_num FROM alembic_version")] if has_alembic else [])
        report = {
            'captured_at_utc': datetime.now(UTC).isoformat(),
            'source_path': str(source),
            'snapshot_path': str(destination),
            'snapshot_sha256': _sha256(destination),
            'snapshot_size_bytes': destination.stat().st_size,
            'sqlite_integrity_check': 'ok',
            'alembic_versions': versions,
            'source_file_metadata_before': source_metadata_before,
            'data_policy': 'PRIVATE: do not commit snapshot or manifest to GitHub',
            'limitations': ('This is a logical point-in-time snapshot. Stop all writers '
                            'and take an additional offline copy before evaluating '
                            'conflicting research evaluations or migrating a restored copy.'),
        }
        # The manifest must also not overwrite an existing file.
        mfd = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        manifest_created = True
        with os.fdopen(mfd, 'w', encoding='utf-8') as file:
            json.dump(report, file, indent=2, sort_keys=True)
            file.write('\n')
        success = True
        return report
    finally:
        if not success:
            destination.unlink(missing_ok=True)
            # A partial report must never be mistaken for successful verification.
            if manifest_created:
                manifest.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args(argv)
    try:
        report = snapshot(args.source, args.destination)
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f'SNAPSHOT_FAILED: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
