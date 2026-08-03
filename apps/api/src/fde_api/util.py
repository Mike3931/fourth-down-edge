"""Small shared utilities: hashing, git metadata, clock."""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote


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


# Environment variables whose values must never reach a log line, an error
# summary, or the database. Listed here rather than at each call site so a
# new secret is protected everywhere by adding one entry.
SECRET_ENV_VARS = ("FDE_ODDS_API_KEY", "FDE_API_TOKEN")


def redact_secrets(text: str | None) -> str | None:
    """Strip any configured secret out of a string.

    Defense in depth, not the primary control. The odds adapter already
    scrubs its own errors, but `error_summary` is built from arbitrary
    exceptions and PERSISTED, so a credential that ends up in an exception
    message anywhere would otherwise be written to the database. Applied at
    the point of persistence, this covers handlers that do not exist yet.

    Also catches the percent-encoded form: a credential carried in a URL
    query string appears encoded, and a plain substring check would miss it.
    """
    if not text:
        return text
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        for form in (value, quote(value, safe="")):
            text = text.replace(form, f"***{name}_REDACTED***")
    return text
