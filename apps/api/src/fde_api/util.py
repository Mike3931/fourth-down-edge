"""Small shared utilities: hashing, git metadata, clock."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def current_code_commit() -> str:
    """The repository commit the running code was ingested/trained under.

    Falls back to "unknown" outside a git checkout (e.g. a deployed wheel);
    manifests must still be writable there.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def dependency_lock_hash() -> str:
    """Hash of uv.lock, tying artifacts to the exact dependency set."""
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    if lock.exists():
        return sha256_file(lock)
    return "unknown"
