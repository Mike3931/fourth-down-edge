# Governed dependency-advisory exceptions

Every entry here is a written applicability decision, not a suppression.
An advisory may only be added after the analysis below is completed and
recorded. The CI dependency gate lists these IDs explicitly so it stays red
for anything new.

---

## PYSEC-2026-113 — PyArrow

**Decision:**

```
NOT APPLICABLE — affected pre-buffering API is not exposed or invoked through this Python application.
```

| field | value |
| --- | --- |
| Resolved version | `pyarrow 21.0.0` (declared `pyarrow~=21.0.0`) |
| Fixed in | 23.0.1 |
| Reviewed | 2026-08-03 |
| Reviewer | model-integrity gate, `fix/phase-2-replay-integrity` |
| Re-evaluate | on any change that introduces Arrow IPC/Feather reads, a file-upload endpoint, or a native extension; otherwise at the next dependency-policy revision |

### Evidence

**Does any C++ extension directly invoke `RecordBatchFileReader::PreBufferMetadata`?**
No. There is no direct `pyarrow` call anywhere in `apps/api/src` — a grep
for `pyarrow` returns nothing. (A grep for `pa.` matches only a local list
named `pa` for *points-against* in `models_ml/baselines.py`, which is not
the Arrow module.) The project ships no native extension of its own.

**Are Arrow IPC files accepted from untrusted users?**
No. The advisory concerns the Arrow **IPC** format (`.arrow` / Feather).
This codebase reads **Parquet only**, via Polars `pl.scan_parquet`, in
`canonical/load_pbp.py` and `canonical/load_rosters.py`. Those files come
from the immutable raw-artifact store, sourced from nflverse under a
pinned, content-hashed manifest — not from users.

**Does any native component enable IPC pre-buffering?**
No. Nothing in the codebase configures pre-buffering, and the affected
reader is never constructed.

**Is there any upload path at all?**
No. Confirmed independently during the Starlette audit: no `UploadFile`,
no `File(...)`, no `Form(...)`, no multipart route, and `python-multipart`
is not an installed dependency. No endpoint accepts a file of any format.

### Why the lock was not changed

`uv.lock` is hashed into the frozen forward-test policy `ftp-2026-v1` via
`policy.dependency_lock_hash` and into every model-registry entry. Bumping
PyArrow to clear a scanner finding that does not apply would alter that
hash for no security benefit, which is an ungoverned change to a frozen
artifact. The upgrade to 23.0.1 or later is deferred to a dependency-policy
revision that re-freezes deliberately.

---

## PYSEC-2026-1845 — pytest

**Decision:**

```
NOT APPLICABLE — development-only dependency; not present in any runtime path.
```

| field | value |
| --- | --- |
| Resolved version | `pytest 8.4.2` |
| Fixed in | 9.0.3 |
| Reviewed | 2026-08-03 |
| Re-evaluate | when the test toolchain is next upgraded |

`pytest` is a test runner. It is not imported by `fde_api` at runtime, is
not deployed, and cannot be reached by any request. It is carried in the
lock because the lock covers the development environment.

---

## Cleared — no longer exceptions

| ID | Package | Cleared by |
| --- | --- | --- |
| PYSEC-2026-161 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-248 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-249 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-1942 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-2280 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-2281 | starlette | 0.48.0 → 1.3.1 |

Upgraded on the Phase 3 branch (`9ca85e6`) together with
`fastapi 0.116.2 → 0.141.1`. **Not backported to the Phase 2 lineage**:
the Phase 2 test environment resolves and passes on its own lock, so the
dependency change is not required there, and mixing it into the analytical
patch would violate the separation this gate requires.
