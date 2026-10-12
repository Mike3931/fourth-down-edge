"""No third-party dependencies: test real SQLite snapshot semantics."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/snapshot_capture_database.py'


def run(src: Path, dst: Path):
    return subprocess.run([sys.executable, str(SCRIPT), str(src), str(dst)],
                          text=True, capture_output=True, check=False)


def create(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE alembic_version (version_num TEXT)')
        con.execute("INSERT INTO alembic_version VALUES ('b7e2f9c41a68')")
        con.execute('CREATE TABLE forward_ledger (id TEXT PRIMARY KEY, model_probability REAL)')
        con.execute('INSERT INTO forward_ledger VALUES (?, ?)', ('evidence', 0.53))


def test_backup_is_faithful_and_does_not_modify_source(tmp_path):
    src, dst = tmp_path / 'source.db', tmp_path / 'backup.db'
    create(src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    result = run(src, dst)
    assert result.returncode == 0, result.stderr
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    with sqlite3.connect(dst) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert con.execute('SELECT id, model_probability FROM forward_ledger').fetchone() == ('evidence', 0.53)
    manifest = json.loads((tmp_path / 'backup.db.manifest.json').read_text())
    assert manifest['snapshot_sha256'] == hashlib.sha256(dst.read_bytes()).hexdigest()
    assert manifest['alembic_versions'] == ['b7e2f9c41a68']


def test_existing_destination_refused_without_change(tmp_path):
    src, dst = tmp_path / 'source.db', tmp_path / 'backup.db'
    create(src)
    dst.write_bytes(b'protected')
    result = run(src, dst)
    assert result.returncode == 2
    assert dst.read_bytes() == b'protected'


def test_missing_source_refused_without_creating_destination(tmp_path):
    dst = tmp_path / 'backup.db'
    result = run(tmp_path / 'missing.db', dst)
    assert result.returncode == 2
    assert not dst.exists()


def test_wal_committed_records_are_included(tmp_path):
    src, dst = tmp_path / 'source.db', tmp_path / 'backup.db'
    create(src)
    con = sqlite3.connect(src)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('INSERT INTO forward_ledger VALUES (?, ?)', ('wal-entry', 0.42))
        con.commit()
        result = run(src, dst)
        assert result.returncode == 0, result.stderr
        with sqlite3.connect(dst) as backup:
            assert backup.execute('SELECT COUNT(*) FROM forward_ledger').fetchone()[0] == 2
    finally:
        con.close()


def test_existing_manifest_refused_without_creating_snapshot(tmp_path):
    src, dst = tmp_path / 'source.db', tmp_path / 'backup.db'
    create(src)
    manifest = Path(str(dst) + '.manifest.json')
    manifest.write_bytes(b'protected manifest')
    result = run(src, dst)
    assert result.returncode == 2
    assert not dst.exists()
    assert manifest.read_bytes() == b'protected manifest'


def test_wal_snapshot_is_self_contained_and_final_hash_matches(tmp_path):
    src, dst = tmp_path / 'source.db', tmp_path / 'backup.db'
    create(src)
    con = sqlite3.connect(src)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('INSERT INTO forward_ledger VALUES (?, ?)', ('committed', 0.44))
        con.commit()
        result = run(src, dst)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report['snapshot_sha256'] == hashlib.sha256(dst.read_bytes()).hexdigest()
        assert not Path(str(dst) + '-wal').exists()
        assert not Path(str(dst) + '-shm').exists()
        read = sqlite3.connect(f'{dst.as_uri()}?mode=ro&immutable=1', uri=True)
        try:
            assert read.execute('SELECT COUNT(*) FROM forward_ledger').fetchone()[0] == 2
        finally:
            read.close()
    finally:
        con.close()
